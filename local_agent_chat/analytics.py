from __future__ import annotations

import asyncio
import base64
import html
import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .artifacts import Artifact, ArtifactStore
from .sandbox_files import SandboxFiles

_DATASET_SUFFIXES = {".csv", ".tsv", ".xlsx"}


@dataclass(frozen=True, slots=True)
class DatasetInspection:
    text: str


@dataclass(frozen=True, slots=True)
class EDAReportResult:
    artifact: Artifact
    summary: str


class DataAnalysisService:
    """Controlled pandas-based analytics for uploaded CSV/XLSX datasets."""

    def __init__(
        self,
        sandbox: SandboxFiles,
        artifacts: ArtifactStore,
        *,
        max_rows: int = 200_000,
        max_columns: int = 200,
    ) -> None:
        self._sandbox = sandbox
        self._artifacts = artifacts
        self._max_rows = max_rows
        self._max_columns = max_columns

    async def inspect_dataset(
        self,
        *,
        chat_id: str,
        path: str,
        sheet_name: str | None = None,
        sample_rows: int = 5,
    ) -> DatasetInspection:
        safe_sample_rows = max(1, min(int(sample_rows), 10))
        return await asyncio.to_thread(
            self._inspect_sync,
            chat_id,
            path,
            sheet_name,
            safe_sample_rows,
        )

    async def build_eda_report(
        self,
        *,
        chat_id: str,
        path: str,
        target: str | None = None,
        sheet_name: str | None = None,
    ) -> EDAReportResult:
        html_report, summary, filename = await asyncio.to_thread(
            self._build_report_sync,
            chat_id,
            path,
            target,
            sheet_name,
        )
        artifact = await self._artifacts.write_text(
            chat_id,
            name=filename,
            content=html_report,
        )
        return EDAReportResult(artifact=artifact, summary=summary)

    def _inspect_sync(
        self,
        chat_id: str,
        path: str,
        sheet_name: str | None,
        sample_rows: int,
    ) -> DatasetInspection:
        file_path = self._sandbox.resolve_file(chat_id, path)
        frame, meta = self._load_dataset(file_path, sheet_name=sheet_name)

        missing = frame.isna().sum().sort_values(ascending=False)
        missing = missing[missing > 0].head(12)
        dtype_counts = frame.dtypes.astype(str).value_counts()

        lines = [
            f"Dataset: {file_path.name}",
            f"Rows: {len(frame):,}",
            f"Columns: {frame.shape[1]:,}",
            f"Duplicate rows: {int(frame.duplicated().sum()):,}",
            f"Source: {meta}",
            "",
            "Dtype counts:",
        ]
        for dtype, count in dtype_counts.items():
            lines.append(f"- {dtype}: {int(count)}")

        if missing.empty:
            lines.extend(["", "Missing values: none detected"])
        else:
            lines.extend(["", "Columns with most missing values:"])
            for column, count in missing.items():
                pct = float(count) / max(len(frame), 1) * 100
                lines.append(f"- {column}: {int(count)} ({pct:.2f}%)")

        lines.extend(["", f"Sample ({sample_rows} rows):"])
        sample = frame.head(sample_rows).copy()
        for column in sample.columns:
            sample[column] = sample[column].map(_compact_value)
        lines.append(sample.to_string(index=False, max_colwidth=80))

        return DatasetInspection(text="\n".join(lines))

    def _build_report_sync(
        self,
        chat_id: str,
        path: str,
        target: str | None,
        sheet_name: str | None,
    ) -> tuple[str, str, str]:
        file_path = self._sandbox.resolve_file(chat_id, path)
        frame, meta = self._load_dataset(file_path, sheet_name=sheet_name)

        if target and target not in frame.columns:
            raise ValueError(f"Target column {target!r} is not present in the dataset")

        numeric_columns = list(frame.select_dtypes(include=[np.number]).columns)
        categorical_columns = [
            column for column in frame.columns if column not in numeric_columns
        ]
        duplicates = int(frame.duplicated().sum())
        missing_counts = frame.isna().sum().sort_values(ascending=False)
        missing_nonzero = missing_counts[missing_counts > 0]
        constants = [
            column
            for column in frame.columns
            if frame[column].nunique(dropna=False) <= 1
        ]
        high_cardinality = [
            column
            for column in categorical_columns
            if frame[column].nunique(dropna=True) > max(50, int(len(frame) * 0.5))
        ]

        overview = pd.DataFrame(
            [
                ("Rows", f"{len(frame):,}"),
                ("Columns", f"{frame.shape[1]:,}"),
                ("Duplicate rows", f"{duplicates:,}"),
                ("Numeric columns", len(numeric_columns)),
                ("Categorical/other columns", len(categorical_columns)),
                ("Columns with missing values", int((missing_counts > 0).sum())),
                ("Constant columns", len(constants)),
                ("Source", meta),
            ],
            columns=["Metric", "Value"],
        )

        column_summary = pd.DataFrame(
            {
                "column": frame.columns,
                "dtype": [str(dtype) for dtype in frame.dtypes],
                "missing": [
                    int(frame[column].isna().sum()) for column in frame.columns
                ],
                "missing_pct": [
                    round(float(frame[column].isna().mean()) * 100, 2)
                    for column in frame.columns
                ],
                "unique": [
                    int(frame[column].nunique(dropna=True)) for column in frame.columns
                ],
            }
        )

        sections: list[str] = [
            _html_header(f"EDA report — {file_path.name}"),
            f"<h1>EDA report: {html.escape(file_path.name)}</h1>",
            f"<p class='muted'>Generated from {html.escape(meta)}</p>",
            "<h2>Overview</h2>",
            _table_html(overview),
            "<h2>Column quality</h2>",
            _table_html(column_summary),
        ]

        if not missing_nonzero.empty:
            sections.extend(
                [
                    "<h2>Missingness</h2>",
                    _image_html(_missingness_chart(frame)),
                ]
            )

        if numeric_columns:
            describe = (
                frame[numeric_columns]
                .describe()
                .T.reset_index()
                .rename(columns={"index": "column"})
            )
            sections.extend(
                [
                    "<h2>Numeric summary</h2>",
                    _table_html(describe.round(4)),
                ]
            )
            for column in numeric_columns[:6]:
                image = _numeric_histogram(frame[column], column)
                if image:
                    sections.extend(
                        [
                            f"<h3>Distribution: {html.escape(str(column))}</h3>",
                            _image_html(image),
                        ]
                    )

        if categorical_columns:
            categorical_rows: list[dict[str, Any]] = []
            for column in categorical_columns[:12]:
                values = (
                    frame[column]
                    .astype("string")
                    .fillna("<NA>")
                    .value_counts(dropna=False)
                    .head(8)
                )
                for value, count in values.items():
                    categorical_rows.append(
                        {
                            "column": column,
                            "value": _compact_value(value),
                            "count": int(count),
                            "share_pct": round(
                                int(count) / max(len(frame), 1) * 100, 2
                            ),
                        }
                    )
            if categorical_rows:
                sections.extend(
                    [
                        "<h2>Categorical top values</h2>",
                        _table_html(pd.DataFrame(categorical_rows)),
                    ]
                )

        correlation_pairs = (
            _top_correlations(frame[numeric_columns])
            if len(numeric_columns) >= 2
            else []
        )
        if correlation_pairs:
            corr_table = pd.DataFrame(
                correlation_pairs,
                columns=["feature_1", "feature_2", "correlation"],
            )
            sections.extend(
                [
                    "<h2>Strongest numeric correlations</h2>",
                    _table_html(corr_table),
                    _image_html(_correlation_heatmap(frame[numeric_columns])),
                ]
            )

        if target:
            sections.append(f"<h2>Target: {html.escape(target)}</h2>")
            target_image, target_table = _target_section(frame[target], target)
            if target_table is not None:
                sections.append(_table_html(target_table))
            if target_image:
                sections.append(_image_html(target_image))

        sample = frame.head(20).copy()
        for column in sample.columns:
            sample[column] = sample[column].map(_compact_value)
        sections.extend(
            [
                "<h2>Sample rows</h2>",
                _table_html(sample),
                "</main></body></html>",
            ]
        )

        summary_lines = [
            f"EDA completed for {file_path.name}: {len(frame):,} rows, {frame.shape[1]} columns.",
            f"Duplicates: {duplicates:,}.",
            f"Columns with missing values: {int((missing_counts > 0).sum())}.",
        ]
        if not missing_nonzero.empty:
            top = missing_nonzero.head(5)
            summary_lines.append(
                "Top missing columns: "
                + ", ".join(
                    f"{column}={int(count)} ({float(count) / max(len(frame), 1) * 100:.1f}%)"
                    for column, count in top.items()
                )
                + "."
            )
        if constants:
            summary_lines.append(
                "Constant columns: " + ", ".join(map(str, constants[:10])) + "."
            )
        if high_cardinality:
            summary_lines.append(
                "High-cardinality categorical columns: "
                + ", ".join(map(str, high_cardinality[:10]))
                + "."
            )
        if correlation_pairs:
            a, b, value = correlation_pairs[0]
            summary_lines.append(
                f"Strongest numeric correlation: {a} ↔ {b} = {value:.3f}."
            )
        if target:
            summary_lines.append(f"Target section included for {target!r}.")

        filename = f"eda_{_safe_stem(file_path.stem)}.html"
        return "".join(sections), "\n".join(summary_lines), filename

    def _load_dataset(
        self,
        path: Path,
        *,
        sheet_name: str | None,
    ) -> tuple[pd.DataFrame, str]:
        suffix = path.suffix.casefold()
        if suffix not in _DATASET_SUFFIXES:
            raise ValueError("Structured analytics supports CSV, TSV and XLSX files")

        if suffix == ".xlsx":
            workbook = pd.ExcelFile(path, engine="openpyxl")
            chosen_sheet = sheet_name or workbook.sheet_names[0]
            if chosen_sheet not in workbook.sheet_names:
                raise ValueError(
                    f"Unknown sheet {chosen_sheet!r}. Available sheets: {', '.join(workbook.sheet_names)}"
                )
            frame = pd.read_excel(
                workbook,
                sheet_name=chosen_sheet,
                nrows=self._max_rows + 1,
            )
            meta = f"XLSX sheet {chosen_sheet!r}; available sheets: {', '.join(workbook.sheet_names)}"
        else:
            frame = _read_delimited(path, max_rows=self._max_rows + 1)
            meta = "TSV" if suffix == ".tsv" else "CSV (delimiter auto-detected)"

        if len(frame) > self._max_rows:
            raise ValueError(
                f"Dataset exceeds the v1 row limit ({self._max_rows:,}). "
                "Use a smaller extract or raise ANALYST_MAX_DATASET_ROWS."
            )
        if frame.shape[1] > self._max_columns:
            raise ValueError(
                f"Dataset exceeds the v1 column limit ({self._max_columns}). "
                "Use a narrower extract or raise ANALYST_MAX_COLUMNS."
            )
        if frame.empty:
            raise ValueError("Dataset contains no rows")
        if frame.shape[1] == 0:
            raise ValueError("Dataset contains no columns")

        return frame, meta


def _read_delimited(path: Path, *, max_rows: int) -> pd.DataFrame:
    suffix = path.suffix.casefold()
    separator = "\t" if suffix == ".tsv" else None
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return pd.read_csv(
                path,
                sep=separator,
                engine="python" if separator is None else "c",
                encoding=encoding,
                nrows=max_rows,
            )
        except UnicodeDecodeError as error:
            last_error = error
    if last_error is not None:
        raise ValueError(
            "CSV/TSV encoding is not UTF-8/UTF-8-SIG/CP1251"
        ) from last_error
    raise ValueError("Unable to read delimited dataset")


def _compact_value(value: Any) -> str:
    if pd.isna(value):
        return "<NA>"
    text = str(value).replace("\n", " ").replace("\r", " ")
    return text if len(text) <= 120 else text[:117] + "..."


def _safe_stem(stem: str) -> str:
    result = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_" for char in stem
    )
    return result.strip("_")[:80] or "dataset"


def _html_header(title: str) -> str:
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>
<style>
body{{font-family:Arial,sans-serif;margin:0;background:#fafafa;color:#222}}
main{{max-width:1200px;margin:0 auto;padding:32px}}
h1,h2,h3{{margin-top:1.4em}} .muted{{color:#666}}
.table{{border-collapse:collapse;width:100%;font-size:14px;background:white;margin:12px 0 24px}}
.table th,.table td{{border:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}}
.table th{{background:#f1f1f1;position:sticky;top:0}}
img{{max-width:100%;height:auto;background:white;border:1px solid #ddd;margin:8px 0 24px}}
</style></head><body><main>"""


def _table_html(frame: pd.DataFrame) -> str:
    return frame.to_html(index=False, border=0, classes="table", escape=True)


def _fig_to_data_uri(fig: plt.Figure) -> str:
    buffer = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buffer, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _image_html(data_uri: str) -> str:
    return f"<img src='{data_uri}' alt='EDA chart'>"


def _missingness_chart(frame: pd.DataFrame) -> str:
    missing = (frame.isna().mean() * 100).sort_values(ascending=False)
    missing = missing[missing > 0].head(20).sort_values()
    fig, ax = plt.subplots(figsize=(9, max(3, len(missing) * 0.35)))
    ax.barh([str(value) for value in missing.index], missing.values)
    ax.set_xlabel("Missing, %")
    ax.set_title("Columns with missing values")
    return _fig_to_data_uri(fig)


def _numeric_histogram(series: pd.Series, column: Any) -> str | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    fig, ax = plt.subplots(figsize=(8, 4))
    bins = min(40, max(10, int(math.sqrt(len(clean)))))
    ax.hist(clean.to_numpy(), bins=bins)
    ax.set_title(str(column))
    ax.set_xlabel(str(column))
    ax.set_ylabel("Count")
    return _fig_to_data_uri(fig)


def _top_correlations(frame: pd.DataFrame) -> list[tuple[str, str, float]]:
    if frame.shape[1] < 2:
        return []
    corr = frame.corr(numeric_only=True)
    pairs: list[tuple[str, str, float]] = []
    columns = list(corr.columns)
    for i, left in enumerate(columns):
        for right in columns[i + 1 :]:
            value = corr.loc[left, right]
            if pd.notna(value):
                pairs.append((str(left), str(right), float(value)))
    pairs.sort(key=lambda item: abs(item[2]), reverse=True)
    return pairs[:15]


def _correlation_heatmap(frame: pd.DataFrame) -> str:
    selected = frame.loc[:, frame.nunique(dropna=True) > 1]
    if selected.shape[1] > 20:
        variances = selected.var(numeric_only=True).sort_values(ascending=False)
        selected = selected[list(variances.head(20).index)]
    corr = selected.corr(numeric_only=True)
    fig, ax = plt.subplots(figsize=(max(6, len(corr) * 0.45), max(5, len(corr) * 0.4)))
    image = ax.imshow(corr.to_numpy(), vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(len(corr.columns)), [str(c) for c in corr.columns], rotation=90)
    ax.set_yticks(range(len(corr.index)), [str(c) for c in corr.index])
    ax.set_title("Numeric correlation matrix")
    fig.colorbar(image, ax=ax)
    return _fig_to_data_uri(fig)


def _target_section(
    series: pd.Series, target: str
) -> tuple[str | None, pd.DataFrame | None]:
    if pd.api.types.is_numeric_dtype(series):
        clean = pd.to_numeric(series, errors="coerce").dropna()
        if clean.empty:
            return None, None
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.hist(clean.to_numpy(), bins=min(40, max(10, int(math.sqrt(len(clean))))))
        ax.set_title(f"Target distribution: {target}")
        return _fig_to_data_uri(fig), clean.describe().to_frame("value").reset_index()

    values = series.astype("string").fillna("<NA>").value_counts(dropna=False).head(30)
    table = pd.DataFrame(
        {
            "value": [str(value) for value in values.index],
            "count": [int(value) for value in values.values],
            "share_pct": [
                round(int(value) / max(len(series), 1) * 100, 2)
                for value in values.values
            ],
        }
    )
    fig, ax = plt.subplots(figsize=(9, max(3, len(values) * 0.3)))
    ax.barh(
        [str(value) for value in reversed(values.index)], list(reversed(values.values))
    )
    ax.set_title(f"Target distribution: {target}")
    ax.set_xlabel("Count")
    return _fig_to_data_uri(fig), table
