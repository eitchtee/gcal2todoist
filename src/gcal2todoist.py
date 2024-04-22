from time import gmtime
import datetime
import logging
import calendar

from helpers.db import DB
from helpers.decorators import retry, keep_running
from classes.config import Config

# from todoist_api_python.api import Task
from todoist_api_override.api import (
    TaskPatched as Task,
)
from gcsa.event import Event
from dateutil.parser import parse

from markdownify import markdownify


# Initialize logger handle
logging.getLogger("googleapiclient").setLevel(logging.CRITICAL)
logger = logging.getLogger()
handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s %(name)-12s %(levelname)-8s %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


configs = Config()
db = DB()


class TodoistTask:
    def __init__(
        self,
        event: Event,
        date,
        duration: int,
        index: int,
        gcal_id: str,
        todoist_project_id: str,
        todoist_id: str = None,
    ):
        self.event = event

        self.task_name = self.generate_task_name()
        self.note = self.generate_note()

        self.date = date
        self.duration = duration
        self.index = index

        self.gcal_id = gcal_id
        self.todoist_project_id = todoist_project_id

        self.todoist_id = todoist_id

    def generate_task_name(self) -> str:
        return f"{configs.task_prefix}{self.event.summary.strip()}{configs.task_suffix}"

    def generate_note(self) -> str:
        note = []
        location = self.event.location
        description = self.event.description
        hangout_link = self.event.other.get("hangoutLink")
        attendees = self.event.attendees
        attendee_status = {
            "accepted": "🟢",
            "declined": "🔴",
            "needsAction": "⚫",
            "tentative": "🟡",
        }

        if hangout_link:
            note.append(f"📞 {hangout_link}")
        if location:
            note.append(f"📍 {location}")
        if description:
            note.append(f"📝 {markdownify(description)}")
        if attendees:
            result = ["👥 Convidados:\n"]

            for attendee in attendees:
                display_line = [attendee_status[attendee.response_status]]

                if attendee.display_name:
                    display_line.append(attendee.display_name)
                if attendee.email:
                    display_line.append(attendee.email)

                if len(display_line) >= 2:
                    result.append(" - ".join(display_line) + "\n")
            note.append("".join(result))
        if len(note) == 0:
            note.append("")

        note = "\n\n".join(note).strip()

        return note

    def generate_task_date(self) -> dict:
        if type(self.date) is datetime.datetime:
            date = str(self.date.astimezone(datetime.timezone.utc))
            task_date = {"due_date": date}
        else:
            date = str(self.date)
            task_date = {
                "due_datetime": date,
            }

        if self.duration:
            task_date["duration"] = self.duration
            task_date["duration_unit"] = "minute"

        return task_date

    def add(self) -> None:
        task_date = self.generate_task_date()

        task = add_task(
            content=self.task_name,
            description=self.note,
            project_id=self.todoist_project_id,
            labels=[configs.label],
            **task_date,
        )

        db.update_todoist_id(
            todoist_id=task.id, event_id=self.event.event_id, event_index=self.index
        )

    def update(self, existing_tasks: list[Task]) -> None:
        tasks_on_todoist = list(
            filter(
                lambda obj: obj.id == self.todoist_id,
                existing_tasks,
            )
        )
        task_on_todoist = tasks_on_todoist[0] if tasks_on_todoist else None

        if task_on_todoist:
            if (
                configs.completed_label in task_on_todoist.labels
                and not task_on_todoist.is_completed
            ):
                logger.info("- Forcefully completing labeled task")
                close_task(task_id=self.todoist_id)
                db.update_todoist_status(
                    completed=True, event_id=self.event.event_id, event_index=self.index
                )

            elif (
                task_on_todoist.content != self.task_name
                or task_on_todoist.description != self.note
                or (
                    type(self.date) is datetime.date
                    and task_on_todoist.due.date
                    and task_on_todoist.due.string != str(self.date)
                )
                or (
                    type(self.date) is datetime.datetime
                    and task_on_todoist.due.datetime
                    and parse(task_on_todoist.due.datetime) != self.date
                )
                or (
                    task_on_todoist.duration
                    and task_on_todoist.duration.amount != self.duration
                )
            ):
                logger.info("- Updating task")

                task_date = self.generate_task_date()

                update_task(
                    task_id=self.todoist_id,
                    content=self.task_name,
                    description=self.note,
                    project_id=self.todoist_project_id,
                    **task_date,
                )

        else:
            self.add()


def generate_date_range(event: Event) -> list[tuple]:
    date_range = []

    start = 0  # Range start
    end = (event.end - event.start).days  # Range end

    if end >= 1:
        end += 1  # Catch multi-day events

    for x in range(start, end):
        if x == start:
            start_date = event.start + datetime.timedelta(days=x)
        else:
            start_date = event.start + datetime.timedelta(days=x)
            if type(event.start) == datetime.datetime:
                # if a multi-day event start and end times, set the start time to midnight on the second day forward
                start_date = start_date.replace(hour=0, minute=0, second=0)

        if start_date >= event.end:
            continue

        duration = event.end - start_date
        duration = int(duration.total_seconds() / 60)

        if duration > 1440:  # Todoist tasks has a maximum duration of 24 hours
            duration = None

        date_range.append((start_date, duration, x))

    if not date_range:
        duration = event.end - event.start
        duration = int(duration.total_seconds() / 60)
        if duration >= 1440:  # Todoist tasks has a maximum duration of 24 hours
            duration = None
        date_range.append((event.start, duration, 0))

    return date_range


def should_add_based_on_date(
    date: datetime.date | datetime.datetime, duration: int
) -> bool:
    duration = duration if duration else 0

    if (type(date) is datetime.date and date < datetime.datetime.today().date()) or (
        type(date) is datetime.datetime
        and date
        < datetime.datetime.now().replace(tzinfo=date.tzinfo)
        - datetime.timedelta(
            minutes=duration
        )  # This keeps the event on Todoist until its proper end
    ):
        return False

    return True


def should_add_based_on_event(event: Event, gcal_id: str) -> bool:
    event_atendees = {
        atendee.email: atendee.response_status for atendee in event.attendees
    }

    if (
        event_atendees
        and gcal_id in event_atendees.keys()
        and event_atendees[gcal_id] not in ["accepted", "needsAction", "tentative"]
    ):
        return False

    return True


@retry()
def delete_task(task_id: str) -> None:
    configs.todoist.delete_task(task_id=task_id)


@retry()
def get_tasks(project_id: str) -> list[Task]:
    return configs.todoist.get_tasks(project_id=project_id)


@retry()
def add_task(*args, **kwargs) -> Task:
    return configs.todoist.add_task(*args, **kwargs)


@retry()
def update_task(*args, **kwargs) -> None:
    configs.todoist.update_task(*args, **kwargs)


@retry()
def close_task(task_id: str) -> None:
    configs.todoist.close_task(task_id=task_id)


@keep_running(one_shot=not configs.keep_running, delay=configs.run_every)
def run() -> None:
    run_id = calendar.timegm(gmtime())
    logger.info(f"Run {run_id}")

    for todoist_project_id, gcal_ids in configs.get_calendars():
        existing_tasks = get_tasks(project_id=todoist_project_id)

        for gcal_id in gcal_ids:
            for event in configs.get_calendar_events(gcal_id=gcal_id):
                for date, duration, index in generate_date_range(event):
                    logger.info(f"Handling task '{event.summary}'[{index}]")

                    if not should_add_based_on_event(event=event, gcal_id=gcal_id):
                        logger.info("- Skipping due to event")
                        continue

                    if not should_add_based_on_date(date, duration=duration):
                        logger.info("- Skipping due to date")
                        continue

                    db.insert_or_update_without_todoist(
                        event_id=event.event_id,
                        due_date=date,
                        event_index=index,
                        run_id=run_id,
                    )

                    db_event = db.get_event(event_id=event.event_id, event_index=index)

                    if db_event and db_event.get("todoist_id"):
                        if not db_event.get("completed"):
                            existing_task = TodoistTask(
                                event=event,
                                date=date,
                                duration=duration,
                                index=index,
                                gcal_id=gcal_id,
                                todoist_project_id=todoist_project_id,
                                todoist_id=db_event.get("todoist_id"),
                            )
                            existing_task.update(existing_tasks=existing_tasks)
                        else:
                            logger.info("- Task is considered done.")

                    else:
                        logger.info("- Adding task")
                        new_task = TodoistTask(
                            event=event,
                            date=date,
                            duration=duration,
                            index=index,
                            gcal_id=gcal_id,
                            todoist_project_id=todoist_project_id,
                        )
                        new_task.add()

    logger.info("Starting cleanup")

    for entry in db.get_unattached_events(run_id=run_id):
        # Delete all DB entries that weren't updated with the current run_id because they've either become stale or
        # unattached somehow
        if entry.get("todoist_id"):
            delete_task(task_id=entry.get("todoist_id"))

        db.delete_event(entry.doc_id)


if __name__ == "__main__":
    run()
