import logging
import os
import datetime
from typing import Iterator

import yaml
from gcsa.event import Event

from todoist_api_python.api import TodoistAPI
from gcsa.google_calendar import GoogleCalendar


logger = logging.getLogger()


def flatten_paginated(iterator) -> list:
    """Flatten a paginated iterator into a single list."""
    result = []
    for page in iterator:
        result.extend(page)
    return result


class Config:
    def __init__(self):
        self.mother_project_name = None
        self.mother_project_id = None

        self.label = None
        self.keep_running = None
        self.run_every = None

        self.task_prefix = None
        self.task_suffix = None
        self.completed_label = None

        self.days_to_fetch = None

        self.todoist_token = None
        self.todoist = None

        configs_path = "configs/configs.yml"
        if os.path.isfile(configs_path):
            with open(configs_path, encoding="utf8") as file:
                data = yaml.load(file, Loader=yaml.FullLoader)
        else:
            data = os.environ

        log_level = data.get("log_level", "INFO")
        logger.setLevel(log_level)

        self.todoist_token = data.get("todoist_api_token")
        self.mother_project_name = data.get("default_project", "Events")
        self.label = data.get("label", "Event")
        self.keep_running = data.get("keep_running", True)
        self.run_every = data.get("run_every", 300)
        self.task_prefix = data.get("task_prefix", "* 🗓️ ```")
        self.task_suffix = data.get("task_suffix", "```")
        self.completed_label = data.get("completed_label", "Done")
        self.days_to_fetch = data.get("days_to_fetch", "7")

        if not self.todoist_token:
            raise Exception("Todoist token not set.")

        self.todoist = TodoistAPI(self.todoist_token)

        self.fetch_mother_project_id()

    def fetch_mother_project_id(self) -> None:
        """Fetch the default_project ID from Todoist and set it as an attribute"""

        logger.info("Fetching mother project-id")
        all_projects = flatten_paginated(self.todoist.get_projects())
        matching_projects = [
            project
            for project in all_projects
            if project.name == self.mother_project_name
        ]
        if len(matching_projects) >= 1:
            proj_id = matching_projects[
                0
            ].id  # Always return the first project listed by Todoist
        else:
            new_project = self.todoist.add_project(self.mother_project_name)
            proj_id = new_project.id

        logger.info(f"Mother project-id found: {proj_id}")

        self.mother_project_id = proj_id

    def get_calendars(self) -> tuple[str, list]:
        """Search for Todoist projects with a calendar comment and yield them"""

        all_projects = flatten_paginated(self.todoist.get_projects())
        # Filter to child projects of mother project
        child_projects = [
            x for x in all_projects if x.parent_id == self.mother_project_id
        ]

        for project in child_projects:
            gcal_calendar_ids = []
            comments = flatten_paginated(
                self.todoist.get_comments(project_id=project.id)
            )

            for comment in comments:
                gcal_calendar_ids.append(comment.content)

            # Only yield projects that have at least one comment (calendar ID)
            if gcal_calendar_ids:
                yield project.id, gcal_calendar_ids

    def get_calendar_events(self, gcal_id: str) -> Iterator[Event]:
        """Yield events from a calendar"""

        gc = GoogleCalendar(
            gcal_id, credentials_path=".credentials/credentials.json"
        ).get_events(
            time_min=datetime.datetime.today(),
            time_max=datetime.datetime.today()
            + datetime.timedelta(
                days=self.days_to_fetch,
            ),
            single_events=True,
        )

        logger.info(f'Getting calendar: "{gcal_id}"')

        for event in list(gc):
            yield event
