from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from hammers import network_ip_cleaner
import logging
import time


class IpCleanerJob(object):
    cloud = "envvars"

    default_arg_list = [
        "--dry-run",
        "--clean-networks",
        "--clean-floatingips",
        "--clean-routers",
    ]
    ip_cleaner_args = []

    trigger = None
    jobid = None
    name = None

    def __init__(self, cloud: str, jobid, name) -> None:
        self.cloud = cloud
        self.trigger = IntervalTrigger(minutes=1, jitter=15)
        self.jobid = jobid
        self.name = name

        self.ip_cleaner_args = ["--cloud", self.cloud]
        self.ip_cleaner_args.extend(self.default_arg_list)

    def get_jobargs(self):
        return {
            "id": self.jobid,
            "name": self.name,
            "func": network_ip_cleaner.main,
            "kwargs": {"arg_list": self.ip_cleaner_args},
            "trigger": self.trigger,
        }


def scheduler():
    # start with memoryjobstore named default
    # and threadpoolexecutor named default with count=10
    scheduler = BackgroundScheduler()

    # start it up
    scheduler.start()

    ip_cleaner = IpCleanerJob(cloud="envvars", jobid="ip_cleaner", name="ip_cleaner")

    scheduler.add_job(**ip_cleaner.get_jobargs())

    # Keep running
    logging.info("Scheduler running. Press Ctrl+C to exit.")
    while True:
        time.sleep(60)
