from langgraph.checkpoint.memory import InMemorySaver


def create_checkpointer():
    # InMemorySaver() - dev вариант, вся память слетает после перезапуска.
    # В будущем будет заменен на SQLite,
    # граф при этом не поменяется
    return InMemorySaver()
