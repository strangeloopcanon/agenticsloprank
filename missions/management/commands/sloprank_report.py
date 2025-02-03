import csv
import datetime

from django.core.management.base import BaseCommand
from django.db.models import CharField
from django.db.models.functions import Lower

from missions.admin_jobs import get_random_repo
from missions.hub import fulfil_mission
from missions.models import Mission, MissionInfo, TaskCategory
from missions.prompts import get_prompt_from_github
from missions.util import AGENT_REPORT_URL, log

CharField.register_lookup(Lower, "lower")


SLOPRANK_MISSION = "SlopRank Repo Analysis"


class Command(BaseCommand):
    help = "Grab a random repo from GitHub's Daily Trending and run a report on it"

    def add_arguments(self, parser):
        parser.add_argument("--repo", help="not a random report if you set the repo")
        parser.add_argument("--llm", help="llm to use")
        parser.add_argument(
            "--file", help="CSV file to append to, for subsequent SlopRanking"
        )

    def handle(self, *args, **options):
        log("Commencing Sloprank report")
        selected = options["repo"] if options["repo"] else get_random_repo(care=False)
        log("Reporting on", selected)
        mission_info = MissionInfo.objects.get(name=SLOPRANK_MISSION)
        mission = mission_info.create_mission()
        if options["llm"]:
            mission.llm = options["llm"]  # by default we use OpenAI if not set
        mission.flags["github"] = selected
        mission.save()

        copy_mission = None
        potential_copy_mission_ids = Mission.objects.filter(
            mission_info_id=mission_info.id, flags__github=selected
        ).values("id")
        for val in potential_copy_mission_ids:
            mission = Mission.objects.get(id=val["id"])
            tasks = mission.task_set.filter(category=TaskCategory.API)
            if not tasks:
                continue
            viable_copy_mission = True
            for task in tasks:
                if viable_copy_mission and not task.response:
                    viable_copy_mission = False
            if viable_copy_mission:
                copy_mission = mission
                log("Found copy mission for raw data", copy_mission)

        fulfil_mission(mission.id, copy_mission)
        log("SlopRank report complete")

        log("Writing to CSV")
        final_report = mission.task_set.filter(url=AGENT_REPORT_URL).first()
        prompt = get_prompt_from_github("risk-analysis")

        rows = []
        file = options["file"] if options["file"] else "output/sloprank.csv"
        try:
            with open(file, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows.extend(reader)
        except FileNotFoundError:
            pass
        rows.append(
            {
                "prompt": prompt,
                "model": final_report.get_llm(),
                "response": final_report.response,
                "is_valid": True,
                "response_time": datetime.datetime.utcnow().isoformat(),
                "Answer_key": "n/a",
                "token_count": len(final_report.response or ""),
                "error": None,
            }
        )
        with open(file, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            if f.tell() == 0:
                writer.writeheader()
            writer.writerows([rows[-1]])
