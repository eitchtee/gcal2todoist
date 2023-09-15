import logging

from tinydb import TinyDB, Query

logger = logging.getLogger()


class DB:
    def __init__(self):
        self.db: TinyDB = TinyDB("events.json")

    def insert_or_update_without_todoist(
        self,
        event_id,
        due_date,
        event_index,
        run_id,
    ):
        event = Query()

        self.db.upsert(
            {
                "event_id": event_id,
                "due_date": str(due_date),
                "event_index": event_index,
                "run_id": run_id,
            },
            ((event.event_id == event_id) & (event.event_index == event_index)),
        )

    def insert_or_update_with_todoist(
        self,
        event_id,
        due_date,
        event_index,
        run_id,
        todoist_id,
    ):
        event = Query()

        self.db.upsert(
            {
                "event_id": event_id,
                "due_date": str(due_date),
                "event_index": event_index,
                "run_id": run_id,
                "todoist_id": todoist_id,
            },
            ((event.event_id == event_id) & (event.event_index == event_index)),
        )

    def update_todoist_id(self, todoist_id, event_id, event_index):
        event = Query()

        self.db.update(
            {
                "todoist_id": todoist_id,
            },
            ((event.event_id == event_id) & (event.event_index == event_index)),
        )

    def update_todoist_status(self, completed: bool, event_id, event_index):
        event = Query()

        self.db.update(
            {
                "completed": completed,
            },
            ((event.event_id == event_id) & (event.event_index == event_index)),
        )

    def get_event(self, event_id, event_index):
        event = Query()

        return self.db.get(
            ((event.event_id == event_id) & (event.event_index == event_index))
        )

    def get_unattached_events(self, run_id):
        event = Query()

        return self.db.search((event.run_id != run_id))

    def delete_event(self, doc_id):
        event = Query()

        return self.db.remove(doc_ids=[doc_id])
