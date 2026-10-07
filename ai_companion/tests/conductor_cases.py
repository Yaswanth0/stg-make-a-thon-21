"""Sample sentences and the label the Conductor should pick for each.
Used by test_conductor.py (keyword shortcuts) and eval_conductor.py (real LLM)."""

CASES = [
    # schedule
    ("remind me to call mom tomorrow at 6 pm", "schedule"),
    ("in 10 minutes remind me to check the oven", "schedule"),
    ("set a reminder for my dentist appointment on Friday", "schedule"),
    ("what are my reminders", "schedule"),
    ("do I have any reminders today", "schedule"),
    ("cancel the reminder about the dentist", "schedule"),
    ("wake me up at seven tomorrow", "schedule"),
    ("set an alarm for 6 30", "schedule"),
    # search
    ("search for the price of a raspberry pi 5", "search"),
    ("look up who won the cricket match yesterday", "search"),
    ("what's the weather like in Chennai today", "search"),
    ("what are today's news headlines", "search"),
    ("how much is bitcoin worth right now", "search"),
    ("who won the latest formula one race", "search"),
    ("google the opening hours of the city library", "search"),
    # remember
    ("remember my locker code is 4521", "remember"),
    ("remember that I parked on level 3", "remember"),
    ("note that my wifi password is sunflower", "remember"),
    ("please remember my sister's birthday is May 4th", "remember"),
    ("don't forget that my doctor is Dr. Rao", "remember"),
    ("my car is parked in spot B12", "remember"),
    ("my favourite colour is blue", "remember"),
    # answer
    ("what's my locker code", "answer"),
    ("do you remember where I parked", "answer"),
    ("what is the capital of France", "answer"),
    ("tell me a joke", "answer"),
    ("how many legs does a spider have", "answer"),
    ("explain what photosynthesis is", "answer"),
    ("what did I just ask you", "answer"),
    ("thank you", "answer"),
    ("how are you today", "answer"),
    ("what's my sister's birthday", "answer"),
]
