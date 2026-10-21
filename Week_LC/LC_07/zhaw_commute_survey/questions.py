"""Question catalogue of the ZHAW commute survey.

Single source of truth: the form, the validation, the database table, the
results page and the exports are all generated from SECTIONS.
A question key is also its database column, so do not rename keys once
the survey is live.

Question types
- choice: one option out of a list (stored as text)
- number: whole number between 'min' and 'max' (stored as integer)
- likert: 5-point agreement scale (stored as 1..5)
"""

LIKERT_LABELS = [
    "Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree",
]

SECTIONS = [
    {
        "title": "Your way to ZHAW",
        "intro": "Think of a typical day when you come to ZHAW.",
        "questions": [
            {"key": "campus", "type": "choice",
             "text": "Where do you mainly study?",
             "options": ["Winterthur", "Zürich", "Wädenswil"]},
            {"key": "transport", "type": "choice",
             "text": "Which means of transport do you mainly use "
                     "(longest part of the trip)?",
             "options": ["Train", "Tram or bus", "Car or motorbike",
                         "Bike or e-bike", "On foot"]},
            {"key": "door_to_door_min", "type": "number", "unit": "minutes",
             "min": 1, "max": 300,
             "text": "How long does it take you door to door, one way?"},
            {"key": "distance_km", "type": "number", "unit": "km",
             "min": 0, "max": 300,
             "text": "Roughly how far is it, one way?"},
            {"key": "days_per_week", "type": "number", "unit": "days",
             "min": 1, "max": 7,
             "text": "On how many days per week do you come to ZHAW?"},
            {"key": "monthly_cost_chf", "type": "number", "unit": "CHF",
             "min": 0, "max": 2000,
             "text": "How much do you spend per month to come to ZHAW?",
             "hint": "Tickets or travel pass, fuel, parking, bike costs. "
                     "Count a travel pass even if someone else pays "
                     "for it."},
            {"key": "travel_pass", "type": "choice",
             "text": "Which public transport pass do you have?",
             "options": ["GA travelcard", "Regional pass (e.g. ZVV)",
                         "Half Fare travelcard only", "No pass"]},
        ],
    },
    {
        "title": "How do you feel about it?",
        "intro": "How much do you agree with the following statements?",
        "questions": [
            {"key": "satisfied", "type": "likert",
             "text": "I am satisfied with my commute to ZHAW."},
            {"key": "stressful", "type": "likert",
             "text": "My commute is stressful."},
            {"key": "productive", "type": "likert",
             "text": "I can use my travel time productively "
                     "(e.g. study, read)."},
        ],
    },
    {
        "title": "About you",
        "intro": "The survey is anonymous.",
        "questions": [
            {"key": "gender", "type": "choice",
             "text": "What is your gender?",
             "options": ["Female", "Male"]},
            {"key": "age", "type": "number", "unit": "years",
             "min": 15, "max": 99,
             "text": "How old are you?"},
            {"key": "living", "type": "choice",
             "text": "Where do you live?",
             "options": ["Own flat", "Shared apartment", "Other"]},
        ],
    },
]

QUESTIONS = [q for section in SECTIONS for q in section["questions"]]

TEACHING_WEEKS_PER_YEAR = 28


def commute_per_year(minutes, days_per_week, monthly_cost):
    """Hours spent commuting (there and back) during the teaching weeks
    of a year, and CHF spent per year."""
    hours = minutes * 2 * days_per_week * TEACHING_WEEKS_PER_YEAR / 60
    return hours, monthly_cost * 12
