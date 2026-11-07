import re
from dataclasses import dataclass


@dataclass
class Job:
    company: str
    title: str
    url: str
    location: str = ""
    desc: str = ""
    source: str = ""


# Cheap title prefilter: only these jobs are sent to the AI judge.
TITLE_RE = re.compile(
    r"\b(qa|q/a|qc|lqa|quality|tester|testers|testing|test|playtest\w*|"
    r"compliance|certification|cert|submissions?|linguists?|linguistic|"
    r"localization|localisation|ukrainian|russian|tcr|trc|verification|validation)\b",
    re.I,
)

# Jobs located in Russia / Belarus are always dropped.
BAD_COUNTRY_RE = re.compile(
    r"\b(russia|belarus|moscow|minsk|saint petersburg|st\. petersburg)\b|россия|беларусь",
    re.I,
)

CLOSED_RE = re.compile(
    r"no longer (available|accepting)|position (has been|is) (filled|closed)|"
    r"vacancy (is )?closed|job (is )?(closed|expired)|posting (has )?expired|"
    r"no longer open|вакансия закрыта|puesto cubierto|offre expir",
    re.I,
)
