# Методика добавления и настройки агентов в Local Agent Chat v1

Эта методичка описывает, как в текущем проекте добавлять новых агентов, подключать им собственные инструменты, память, работу с файлами и RAG, не меняя общий UI и ядро приложения.

Документ рассчитан на текущую архитектуру **Local Agent Chat v1**. В качестве рабочего примера используется агент `Analyst`, потому что он уже показывает полный путь от регистрации агента до специализированных инструментов и генерации результата в виде файла.

---

## 1. Главный принцип проекта

Новый агент не должен становиться отдельным приложением.

В нормальном случае для его добавления **не нужно** менять:

- `app.py`;
- `AgentRuntime`;
- механизм памяти LangGraph;
- Chainlit data layer;
- хранение истории;
- загрузку файлов;
- восстановление чатов после перезапуска.

Новый агент собирается из уже существующих компонентов:

```text
Agent
├── профиль для UI
├── системный промпт
├── модель
├── набор инструментов
└── общий builder LangChain/LangGraph
```

После регистрации агента в `bootstrap.py` Chainlit автоматически показывает его среди доступных профилей.

Именно это является основным критерием того, что шаблон остаётся универсальным: специализация добавляется **поверх платформы**, а не зашивается внутрь неё.

---

# 2. Как устроен путь запроса

Перед разработкой нового агента полезно держать в голове общий поток.

```text
Chainlit UI
    ↓
app.py
    ↓
ChatBinding
    ↓
AgentRuntime
    ↓
AgentRegistry
    ↓
AgentDefinition
    ↓
compiled LangChain/LangGraph agent
    ↓
model + tools + system prompt
```

`app.py` отвечает за интерфейс и связывает текущий чат с выбранным агентом.

`AgentRuntime` выполняет запрос и передаёт агенту текущий контекст.

`AgentRegistry` знает, какие агенты существуют, и один раз собирает граф каждого типа агента.

`AgentDefinition` описывает конкретного агента.

---

# 3. Какие файлы обычно затрагиваются при добавлении агента

Для обычного нового агента минимальный набор выглядит так:

```text
local_agent_chat/
├── agents/
│   └── my_agent.py
│
├── prompts/
│   └── my_agent.py
│
└── bootstrap.py
```

Если агенту нужны новые возможности:

```text
local_agent_chat/
├── my_service.py
└── tools/
    └── my_tools.py
```

Например, `Analyst` добавляет:

```text
analytics.py
artifacts.py
knowledge.py

tools/
├── analytics.py
└── knowledge.py
```

Важно разделять:

- **инструмент агента** — маленькая функция, которую видит LLM;
- **сервис приложения** — Python-код, который реально выполняет работу.

Инструмент не должен превращаться в большой модуль бизнес-логики.

---

# 4. `AgentDefinition` — основная декларация агента

Описание агента находится в:

```text
local_agent_chat/agent_registry.py
```

Текущая структура:

```python
@dataclass(frozen=True, slots=True)
class AgentDefinition:
    profile: AgentProfile
    build: AgentBuilder
    model_factory: ModelFactory
    system_prompt: str
    tools_factory: ToolFactory | None = None
```

Она разделяет две части агента.

## 4.1. То, что нужно интерфейсу

```python
AgentProfile(
    id="analyst",
    label="Analyst",
    description="...",
    default=False,
)
```

Поля:

- `id` — внутренний стабильный идентификатор;
- `label` — название в UI;
- `description` — описание для пользователя;
- `default` — является ли агент профилем по умолчанию.

`id` желательно считать постоянным. Если изменить его после того, как уже существуют сохранённые чаты, старые диалоги будут ссылаться на несуществующего агента.

---

## 4.2. То, что нужно приложению

```python
AgentDefinition(
    profile=...,
    build=...,
    model_factory=...,
    system_prompt=...,
    tools_factory=...,
)
```

Здесь задаются:

- способ сборки агента;
- используемая модель;
- системный промпт;
- доступные инструменты.

В большинстве случаев новый агент использует общий:

```python
build_langchain_agent
```

и отдельный builder писать не требуется.

---

# 5. Шаг 1. Сначала определить задачу агента

Перед написанием кода сформулируй четыре вещи.

Пример для `Analyst`:

```text
Роль:
анализ данных и бизнес-интерпретация

Вход:
CSV/XLSX + опциональная документация PDF/TXT/MD

Действия:
inspect dataset
search methodology
build EDA

Результат:
выводы в чате + HTML-отчёт
```

Для нового агента желательно заранее ответить на вопросы:

1. Какие задачи он должен решать?
2. Какие данные ему нужны?
3. Какие действия он должен уметь выполнять?
4. Какие действия ему **не нужны**?
5. Что должно быть конечным результатом?

Последний вопрос особенно важен. Агент лучше получается, когда у него есть понятный рабочий сценарий, а не просто длинное описание личности.

---

# 6. Шаг 2. Добавить профиль агента

Создадим для примера агента `Research`.

Файл:

```text
local_agent_chat/agents/research.py
```

Минимальный вариант:

```python
from local_agent_chat.agent_registry import AgentProfile
from local_agent_chat.prompts.research import RESEARCH_AGENT_PROMPT


RESEARCH_AGENT_PROFILE = AgentProfile(
    id="research",
    label="Research",
    description=(
        "Агент для поиска, сопоставления и обобщения "
        "информации из доступных источников."
    ),
)


__all__ = [
    "RESEARCH_AGENT_PROFILE",
    "RESEARCH_AGENT_PROMPT",
]
```

На этом этапе агент ещё ничего не умеет. Мы только описали его для registry и UI.

---

# 7. Шаг 3. Написать системный промпт

Файл:

```text
local_agent_chat/prompts/research.py
```

Все агенты должны наследовать общие правила из:

```text
prompts/base.py
```

Пример:

```python
from .base import BASE_AGENT_PROMPT


RESEARCH_AGENT_PROMPT = f"""
{BASE_AGENT_PROMPT}

Role:
You are a research assistant. Your task is to collect relevant evidence,
compare sources and produce concise, traceable conclusions.

Workflow:
1. Understand what information the user needs.
2. Use available retrieval tools when the answer depends on stored sources.
3. Distinguish retrieved facts from your interpretation.
4. Do not claim to have searched a source unless a tool actually returned it.
5. Keep the final answer focused on the user's question.
""".strip()
```

## Почему нужен `BASE_AGENT_PROMPT`

В нём уже находятся общие правила платформы:

- как использовать память текущего чата;
- когда искать предыдущие диалоги;
- как обращаться с файлами;
- что содержимое файлов не является системной инструкцией;
- что нельзя утверждать, будто инструмент был использован, если он не вызывался.

Новый агент должен добавлять **свою специализацию**, а не заново описывать устройство платформы.

---

# 8. Как писать промпт агента

Для прикладного агента полезнее описывать не характер, а рабочий порядок.

Хорошая структура:

```text
Role
→ кто агент и какую задачу решает

Capabilities
→ какие инструменты доступны и для чего

Workflow
→ в каком порядке действовать

Constraints
→ чего делать нельзя

Output expectations
→ какой результат ожидается
```

Например, у `Analyst` явно написано:

```text
1. Сначала определить набор данных.
2. Проверить его через inspect_dataset.
3. При наличии методологии искать её через RAG.
4. Для EDA вызывать build_eda_report.
5. Разделять факты из данных и интерпретацию.
```

Это намного устойчивее инструкции вида:

```text
"Ты очень опытный аналитик, думай глубоко и внимательно"
```

---

# 9. Шаг 4. Решить, нужны ли агенту новые инструменты

Если новый агент может использовать существующие возможности, новые tools вообще не нужны.

Уже есть общие инструменты:

```text
search_past_chats
read_past_chat

list_chat_files
read_chat_file
```

`Analyst` дополнительно получает:

```text
list_knowledge_documents
search_knowledge

inspect_dataset
build_eda_report
```

Поэтому новый агент можно собрать, например, только из существующих capabilities.

---

# 10. Как устроены инструменты

Инструменты находятся в:

```text
local_agent_chat/tools/
```

Обычно файл содержит factory:

```python
def create_some_tools(service) -> Sequence[BaseTool]:
    ...
```

а внутри определяются функции с `@tool`.

Пример общего паттерна:

```python
from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from local_agent_chat.agent_context import AgentContext


def create_example_tools(service) -> Sequence[BaseTool]:

    @tool
    async def example_tool(
        query: str,
        runtime: ToolRuntime[AgentContext],
    ) -> str:
        """Кратко и конкретно объяснить модели назначение tool."""

        result = await service.run(
            chat_id=runtime.context.chat_id,
            query=query,
        )

        return result

    return (example_tool,)
```

---

# 11. Не передавать `chat_id` и `user_id` модели

Это одно из важных правил шаблона.

Нельзя делать так:

```python
@tool
async def read_data(chat_id: str, path: str):
    ...
```

Потому что `chat_id` тогда выбирает сама модель.

Вместо этого используется:

```python
runtime: ToolRuntime[AgentContext]
```

и:

```python
runtime.context.chat_id
runtime.context.user_id
runtime.context.agent_id
```

`AgentContext` создаётся в `AgentRuntime`:

```python
AgentContext(
    user_id=binding.user_id,
    chat_id=binding.chat_id,
    agent_id=binding.agent_id,
)
```

Таким образом область доступа задаёт приложение, а не LLM.

---

# 12. Когда нужен отдельный сервис

Tool должен оставаться тонким.

Плохой вариант:

```python
@tool
async def build_report(...):
    # 300 строк pandas
    # matplotlib
    # filesystem
    # html
```

Хороший вариант:

```text
Tool
 ↓
DataAnalysisService
 ↓
pandas / matplotlib / ArtifactStore
```

Так устроен `Analyst`.

`tools/analytics.py` только принимает вызов модели и передаёт его в:

```text
analytics.py → DataAnalysisService
```

Это даёт несколько преимуществ:

- сервис можно тестировать без LLM;
- tool остаётся понятным;
- бизнес-логика не зависит от LangChain;
- один сервис позже можно использовать из нескольких агентов.

---

# 13. Шаг 5. Подключить инструменты в `bootstrap.py`

`bootstrap.py` — место, где собирается приложение.

Именно здесь создаются общие сервисы:

```python
memory = SQLiteAgentMemory(...)
history = SQLiteRuntimeHistory(...)
files = SandboxFiles(...)
artifacts = ArtifactStore(...)
knowledge = SQLiteKnowledgeBase(...)
analytics = DataAnalysisService(...)
```

И здесь же собираются наборы инструментов.

Текущий общий набор:

```python
def shared_tools():
    return (
        *create_cross_chat_memory_tools(history),
        *create_chat_file_tools(files),
    )
```

Инструменты аналитика:

```python
def analyst_tools():
    return (
        *shared_tools(),
        *create_knowledge_tools(knowledge),
        *create_analytics_tools(analytics),
    )
```

Для `Research` можно сделать:

```python
def research_tools():
    return (
        *shared_tools(),
        *create_knowledge_tools(knowledge),
    )
```

---

# 14. Шаг 6. Зарегистрировать агента

После этого в `bootstrap.py` добавляется:

```python
registry.register(
    AgentDefinition(
        profile=RESEARCH_AGENT_PROFILE,
        build=build_langchain_agent,
        model_factory=create_model,
        system_prompt=RESEARCH_AGENT_PROMPT,
        tools_factory=research_tools,
    )
)
```

Этого достаточно, чтобы новый агент появился в Chainlit UI.

`app.py` изменять не нужно.

---

# 15. Обновить exports в `agents/__init__.py`

Для текущего стиля проекта добавь экспорт:

```python
from .research import (
    RESEARCH_AGENT_PROFILE,
    RESEARCH_AGENT_PROMPT,
)
```

и в `__all__`:

```python
__all__ = [
    ...,
    "RESEARCH_AGENT_PROFILE",
    "RESEARCH_AGENT_PROMPT",
]
```

После этого `bootstrap.py` может импортировать агента так же, как `General` и `Analyst`.

---

# 16. Что происходит после регистрации

При запуске приложения:

```text
create_application()
    ↓
AgentRegistry.register(...)
```

Chainlit вызывает:

```python
application.registry.profiles()
```

и строит список профилей.

Новый чат:

```text
пользователь выбирает Research
        ↓
ChatBinding.agent_id = "research"
        ↓
agent_id сохраняется в metadata чата
```

При первом запросе:

```text
AgentRegistry.get("research")
        ↓
model_factory()
        ↓
tools_factory()
        ↓
build_langchain_agent(...)
        ↓
compiled graph
```

Дальше этот граф переиспользуется всеми чатами с таким `agent_id`.

Состояние разговоров разделяется через `thread_id`, а не через создание отдельного графа на каждый чат.

---

# 17. Когда нужен отдельный builder агента

В большинстве случаев:

```python
build=build_langchain_agent
```

достаточно.

Отдельный builder стоит добавлять только если агенту требуется действительно другая схема выполнения, например:

```text
несколько узлов LangGraph
ручное ветвление
human approval
отдельный planner
специализированный middleware
несколько подагентов
```

Не стоит писать отдельный builder только потому, что у агента другой prompt или другие tools.

Для обычной специализации достаточно `AgentDefinition`.

---

# 18. Модель агента

Сейчас модель создаётся через:

```python
create_model()
```

в:

```text
local_agent_chat/models.py
```

Функция возвращает:

```python
BaseChatModel
```

Поэтому `AgentRegistry` и сами агенты не зависят от Ollama напрямую.

Сейчас:

```text
create_model
    ↓
ChatOllama
```

Позже можно сделать отдельную фабрику:

```python
def create_research_model() -> BaseChatModel:
    ...
```

и зарегистрировать:

```python
model_factory=create_research_model
```

При этом ни runtime, ни UI менять не нужно.

---

# 19. Память текущего чата подключается автоматически

Новый агент не должен самостоятельно хранить:

```python
self.messages = []
```

или вручную передавать всю историю.

Все зарегистрированные агенты получают общий LangGraph checkpointer:

```text
SQLiteAgentMemory
    ↓
checkpoints.sqlite3
```

При выполнении:

```python
config={
    "configurable": {
        "thread_id": binding.memory_thread_id
    }
}
```

LangGraph самостоятельно восстанавливает состояние конкретного диалога.

Поэтому новый агент получает память текущего чата бесплатно, если использует стандартный builder.

---

# 20. Память между чатами — отдельная возможность

Cross-chat memory не является частью checkpointer.

Она реализована через:

```text
runtime-history.sqlite3
        ↓
SQLite FTS5
        ↓
search_past_chats
read_past_chat
```

Чтобы дать её агенту, достаточно включить:

```python
*create_cross_chat_memory_tools(history)
```

в его набор tools.

Не нужно автоматически загружать предыдущие разговоры в каждый prompt.

Такой retrieval используется только тогда, когда модель считает прошлый контекст полезным.

---

# 21. Работа с файлами

Загруженные пользователем файлы сохраняются в:

```text
.local-agent-chat/
└── sandboxes/
    └── <chat_id>/
        └── files/
```

Agent получает read-only инструменты:

```text
list_chat_files
read_chat_file
```

Текущая стабильная v1 поддерживает:

- обычные текстовые файлы;
- PDF с текстовым слоем.

OCR и изображения не входят в v1.

Если новый агент должен читать пользовательские файлы, в его набор инструментов нужно включить:

```python
*create_chat_file_tools(files)
```

---

# 22. Почему нельзя просто дать агенту файловую систему

В базовом шаблоне агент не получает:

```text
open arbitrary path
write_file
rm
shell
python execution
```

Это намеренное ограничение.

У пользователя есть понятная область:

```text
current chat files
```

Агент работает только внутри неё.

Это делает новые агенты проще, безопаснее и предсказуемее.

---

# 23. Генерация файлов агентом

Для результатов используется отдельная область:

```text
sandboxes/<chat_id>/artifacts/
```

Работой с ней занимается:

```text
ArtifactStore
```

Например `Analyst` не получает общий `write_file`.

Вместо этого:

```text
build_eda_report
    ↓
DataAnalysisService
    ↓
ArtifactStore.write_text(...)
    ↓
eda_dataset.html
```

`app.py` сравнивает список артефактов до и после Turn и прикрепляет новые файлы к сообщению Chainlit.

Для нового агента рекомендуется использовать тот же подход:

```text
конкретный tool
    ↓
конкретный service
    ↓
controlled artifact
```

а не давать модели произвольную запись файлов.

---

# 24. Как добавить собственный результат-файл

Предположим, новый агент должен создавать Markdown-отчёт.

Сервис:

```python
class ReportService:
    def __init__(self, artifacts: ArtifactStore):
        self._artifacts = artifacts

    async def create_report(
        self,
        *,
        chat_id: str,
        content: str,
    ):
        return await self._artifacts.write_text(
            chat_id,
            name="report.md",
            content=content,
        )
```

Tool должен вызывать этот сервис.

После успешного Turn `app.py` сам заметит новый artifact и добавит его в ответ.

---

# 25. RAG в текущем проекте

RAG аналитика реализован отдельно от cross-chat memory.

Назначение разное:

```text
cross-chat memory
→ что пользователь и агент обсуждали раньше

knowledge RAG
→ что написано в загруженных документах
```

Текущая реализация:

```text
PDF / TXT / MD
      ↓
text extraction
      ↓
chunking
      ↓
knowledge.sqlite3
      ↓
SQLite FTS5
      ↓
search_knowledge
```

Это простой локальный вариант без отдельной embedding-модели.

---

# 26. Как дать RAG новому агенту

Если агенту нужен текущий knowledge layer, добавь:

```python
*create_knowledge_tools(knowledge)
```

Он получит:

```text
list_knowledge_documents
search_knowledge
```

При этом `SQLiteKnowledgeBase` уже ограничивает поиск:

```text
user_id
+
chat_id
```

Агент не управляет этими параметрами напрямую.

---

# 27. Когда создавать отдельный RAG

Не каждый агент должен использовать один и тот же индекс.

Отдельный retrieval-сервис нужен, если появляются принципиально другие требования, например:

```text
общая корпоративная база документов
vector embeddings
hybrid search
external search API
metadata filters
миллионы документов
```

В таком случае лучше создать новый service + tool factory, сохранив для агента тот же принцип:

```text
agent → search tool → retrieval service
```

Не нужно зашивать конкретную vector DB в `AgentRuntime`.

---

# 28. Пример `Analyst` как эталон прикладного агента

`Analyst` хорошо показывает рекомендуемый способ расширения.

Он не меняет ядро приложения.

Его специализация состоит из:

```text
AgentProfile
    ↓
ANALYST_AGENT_PROMPT
    ↓
analyst_tools()
    ├── shared tools
    ├── knowledge tools
    └── analytics tools
```

Сервисы:

```text
DataAnalysisService
SQLiteKnowledgeBase
ArtifactStore
SandboxFiles
```

Типичный запрос:

```text
"Проведи EDA sample.xlsx.
Используй methodology.pdf при интерпретации.
Target — target."
```

Ожидаемый путь:

```text
inspect_dataset
      ↓
search_knowledge
      ↓
build_eda_report
      ↓
HTML artifact
      ↓
краткий вывод
```

Это хороший шаблон для новых прикладных агентов: несколько узких возможностей, собранных вокруг одной понятной задачи.

---

# 29. Настройки специализированного агента

Если сервису нужны ограничения, их лучше выносить в `settings.py` и `.env.example`.

Например для аналитика:

```env
ANALYST_MAX_DATASET_ROWS=200000
ANALYST_MAX_COLUMNS=200
```

В `Settings`:

```python
analyst_max_dataset_rows: int
analyst_max_columns: int
```

А дальше настройки передаются в сервис при bootstrap:

```python
analytics = DataAnalysisService(
    files,
    artifacts,
    max_rows=settings.analyst_max_dataset_rows,
    max_columns=settings.analyst_max_columns,
)
```

Не стоит читать environment variables непосредственно внутри каждого tool.

---

# 30. Где хранить настройки

Рекомендуемое разделение:

```text
.env
→ конкретные значения окружения

settings.py
→ разбор и проверка настроек

bootstrap.py
→ передача настроек сервисам

agent/tool/service
→ уже готовые значения
```

Так проще переносить приложение из локальной разработки в другое окружение.

---

# 31. Обработка ошибок в tools

Предсказуемые пользовательские ошибки лучше превращать в понятный ответ инструмента.

Например:

```python
try:
    result = await analytics.inspect_dataset(...)
except (FileNotFoundError, ValueError) as error:
    return f"Unable to inspect dataset {path!r}: {error}"
```

Это подходит для случаев:

- неправильное имя файла;
- неизвестный лист Excel;
- неподдерживаемый формат;
- неизвестный target.

Не стоит скрывать неожиданные ошибки приложения широким:

```python
except Exception:
    return "something went wrong"
```

Такие проблемы лучше оставить видимыми в логах и исправить как баг.

---

# 32. Инструмент должен возвращать модели компактный результат

Tool result становится частью контекста модели.

Поэтому нежелательно возвращать:

```text
500 000 строк CSV
огромный JSON
полный DataFrame
весь PDF на 300 страниц
```

Хороший tool возвращает:

```text
краткий summary
+
несколько ключевых значений
+
путь/имя созданного artifact
```

Большие результаты лучше сохранять в artifact и сообщать модели только итог.

Именно так работает `build_eda_report`.

---

# 33. Не смешивать роль агента и возможности платформы

Промпт агента не должен описывать детали реализации вроде:

```text
"В SQLite есть таблица turns_fts"
```

Агенту достаточно знать:

```text
"у тебя есть инструмент поиска по прошлым чатам"
```

Аналогично:

```text
не "используй PyMuPDF"
а "read_chat_file умеет читать PDF с текстовым слоем"
```

Детали реализации должны оставаться в сервисах.

---

# 34. Не дублировать общий функционал

Если два агента используют одну и ту же возможность, она должна находиться в общем service/tool factory.

Например:

```python
def shared_tools():
    return (
        *create_cross_chat_memory_tools(history),
        *create_chat_file_tools(files),
    )
```

Не нужно создавать:

```text
analyst_read_file
research_read_file
manager_read_file
```

если они делают одно и то же.

---

# 35. Когда делать capability только для одного агента

Отдельный tool уместен, если его смысл связан с конкретной задачей.

Например:

```text
build_eda_report
```

имеет смысл для аналитика.

Но:

```text
read_chat_file
```

является общей возможностью платформы.

Полезный вопрос:

> Этот tool описывает конкретную профессию агента или общую возможность чата?

Если второе — лучше сделать его reusable.

---

# 36. Как тестировать нового агента

Тестировать нужно не только итоговый ответ LLM.

Минимальный набор проверок делится на три уровня.

## 36.1. Сервис

Проверить Python-код без LLM.

Например:

```text
DataAnalysisService.inspect_dataset
DataAnalysisService.build_eda_report
```

на небольшом тестовом файле.

## 36.2. Tool

Проверить:

- корректные аргументы;
- ошибки входных данных;
- ограничение текущим `chat_id`;
- компактность результата.

## 36.3. Agent workflow

Проверить реальный пользовательский сценарий:

```text
upload
→ prompt
→ tool calls
→ final answer
→ artifact
```

---

# 37. Проверка нового агента в UI

После регистрации проверь:

1. Агент появился в selector.
2. Новый чат создаётся именно с ним.
3. В другом чате можно выбрать другого агента.
4. После restart выбранный агент не меняется.
5. История сообщений сохраняется.
6. Память текущего чата сохраняется.
7. Нужные tools доступны.
8. Лишние tools недоступны.
9. Удаление чата не падает.
10. Связанные файлы/память удаляются вместе с чатом.

---

# 38. Проверка границ доступа

Для инструмента, который работает с пользовательскими данными, обязательно проверить:

```text
Chat A не читает files Chat B
User A не получает history User B
current chat не попадает в cross-chat search
arbitrary host path недоступен
```

Не полагайся на системный промпт для этих ограничений.

Они должны обеспечиваться Python-кодом через `AgentContext`, sandbox и storage services.

---

# 39. Что не стоит делать при добавлении обычного агента

Не нужно:

```text
создавать второй Chainlit app
копировать AgentRuntime
создавать отдельную БД checkpoint'ов
добавлять if agent == ... в app.py
хранить history внутри объекта агента
создавать graph на каждый чат
передавать user_id/chat_id как model-controlled tool args
```

Если для нового агента приходится массово менять `app.py` или `runtime.py`, скорее всего специализация попала не в тот слой.

---

# 40. Когда изменение ядра всё-таки оправдано

Ядро можно менять, когда появляется функция, которая относится **ко всем агентам**.

Например:

```text
streaming в Chainlit
визуализация tool calls
единый механизм отмены Turn
summarization длинных чатов
revision / rollback
общий tracing
production authentication
```

Это платформенные функции.

А:

```text
SQL query tool
EDA
поиск по Jira
создание отчёта
работа с CRM
```

— это capabilities конкретных агентов или групп агентов.

---

# 41. Полный минимальный рецепт нового агента

## Файл `prompts/research.py`

```python
from .base import BASE_AGENT_PROMPT


RESEARCH_AGENT_PROMPT = f"""
{BASE_AGENT_PROMPT}

Role:
You are a research assistant.

Workflow:
1. Understand the research question.
2. Search available knowledge when evidence is required.
3. Compare retrieved evidence.
4. Separate evidence from interpretation.
5. Give a concise final synthesis.
""".strip()
```

## Файл `agents/research.py`

```python
from local_agent_chat.agent_registry import AgentProfile
from local_agent_chat.prompts.research import RESEARCH_AGENT_PROMPT


RESEARCH_AGENT_PROFILE = AgentProfile(
    id="research",
    label="Research",
    description="Агент для работы с загруженными материалами и исследованиями.",
)


__all__ = [
    "RESEARCH_AGENT_PROFILE",
    "RESEARCH_AGENT_PROMPT",
]
```

## `agents/__init__.py`

```python
from .research import (
    RESEARCH_AGENT_PROFILE,
    RESEARCH_AGENT_PROMPT,
)
```

## `bootstrap.py`

```python
def research_tools():
    return (
        *shared_tools(),
        *create_knowledge_tools(knowledge),
    )


registry.register(
    AgentDefinition(
        profile=RESEARCH_AGENT_PROFILE,
        build=build_langchain_agent,
        model_factory=create_model,
        system_prompt=RESEARCH_AGENT_PROMPT,
        tools_factory=research_tools,
    )
)
```

На этом новый агент уже появится в UI.

---

# 42. Рецепт агента с новой собственной возможностью

Если `Research` должен уметь, например, строить краткий обзор найденных документов через отдельный Python-сервис, схема такая:

```text
research_service.py
        ↓
tools/research.py
        ↓
research_tools()
        ↓
AgentDefinition
```

Не стоит помещать всю реализацию в `agents/research.py`.

`agents/research.py` должен оставаться маленьким описанием профиля.

---

# 43. Чек-лист перед завершением нового агента

Перед тем как считать агента готовым, проверь:

- [ ] У него есть стабильный уникальный `id`.
- [ ] Есть короткое понятное описание для UI.
- [ ] Промпт наследует `BASE_AGENT_PROMPT`.
- [ ] В промпте описана конкретная задача и порядок работы.
- [ ] Агент получает только реально нужные tools.
- [ ] Общие tools переиспользуются, а не дублируются.
- [ ] Тяжёлая логика вынесена из tools в services.
- [ ] `user_id` и `chat_id` берутся через `ToolRuntime[AgentContext]`.
- [ ] Агент не получает произвольный доступ к host filesystem.
- [ ] Большие результаты сохраняются как artifacts.
- [ ] Ограничения вынесены в settings/.env.
- [ ] Новый агент зарегистрирован только в `bootstrap.py`.
- [ ] Для его добавления не потребовалось менять `AgentRuntime`.
- [ ] После restart старый чат восстанавливается с тем же agent id.
- [ ] Удаление чата корректно очищает его состояние.

---

# 44. Рекомендуемый порядок разработки нового агента

Практически удобнее идти так:

```text
1. Сформулировать один acceptance-сценарий.
2. Создать профиль и prompt.
3. Подключить существующие tools.
4. Проверить agent workflow.
5. Добавить недостающий service.
6. Обернуть service в tool.
7. Добавить ограничения/config.
8. Проверить persistence и isolation.
9. Только потом расширять сценарии.
```

Не стоит сразу проектировать десять tools «на будущее».

`Analyst` стал полезным не потому, что у него много инструментов, а потому что его инструменты покрывают один законченный путь:

```text
data → inspect → methodology → EDA → report
```

---

# 45. Как понять, что новый агент хорошо вписался в шаблон

Хороший признак:

```text
новый agent =
profile
+ prompt
+ tools composition
+ при необходимости несколько services
```

Плохой признак:

```text
новый agent =
новый runtime
+ новый app.py
+ новая память
+ отдельный lifecycle
+ много if/else по agent_id
```

Главная задача архитектуры Local Agent Chat — сделать первый вариант нормой.

---

# 46. Куда расширять шаблон после v1

После появления новых агентов имеет смысл развивать платформу отдельно от их специализации.

Следующие общие улучшения могут включать:

```text
streaming ответов
Chainlit Steps для tool calls
summarization длинной памяти
revision / rollback
OCR/images
безопасное выполнение Python
общий vector/hybrid retrieval
production auth
Postgres persistence
tracing / observability
```

Но ни одно из них не требуется для добавления обычного нового агента в текущую v1.

---

# 47. Краткая памятка

Если нужно добавить нового агента, в большинстве случаев достаточно:

```text
1. prompts/<agent>.py
2. agents/<agent>.py
3. при необходимости service + tools/<agent>.py
4. зарегистрировать AgentDefinition в bootstrap.py
```

Всё остальное уже предоставляет платформа:

```text
UI
выбор агента
память текущего чата
история
resume
cross-chat memory
файлы
изоляция chat/user
model lifecycle
удаление состояния
```

Поэтому новая прикладная логика должна максимально оставаться **над** платформенным слоем, а не проникать внутрь него.
