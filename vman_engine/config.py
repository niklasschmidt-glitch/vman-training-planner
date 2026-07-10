MARK_STATS = [
    "Hurtighed",
    "Acceleration",
    "Udholdenhed",
    "Aflevering",
    "Afslutning",
    "Dribling",
    "Tackling",
    "Dødboldsituationer",
    "Lederskab",
    "Kampånd",
]

GOALKEEPER_STATS = [
    "Håndtering",
    "I luften",
    "Spring",
    "En mod en",
    "Hurtighed",
    "Acceleration",
    "Aflevering",
    "Udholdenhed",
    "Kampånd",
    "Lederskab",
]

POSITION_STATS = {
    "Forsvar": MARK_STATS,
    "Midtbane": MARK_STATS,
    "Angreb": MARK_STATS,
    "Keepere": GOALKEEPER_STATS,
}

POSITION_ALIASES = {
    "Målmand": "Keepere",
    "Mål": "Keepere",
    "Keeper": "Keepere",
    "Defender": "Forsvar",
    "Midfielder": "Midtbane",
    "Forward": "Angreb",
}

POSITION_GROUP = {
    "Forsvar": "Markspiller",
    "Midtbane": "Markspiller",
    "Angreb": "Markspiller",
    "Keepere": "Keepere",
}

XP_TO_NEXT = {
    0: 50, 1: 50, 2: 51, 3: 52, 4: 54, 5: 55, 6: 56, 7: 57, 8: 59, 9: 60,
    10: 61, 11: 63, 12: 64, 13: 65, 14: 67, 15: 68, 16: 70, 17: 71, 18: 72, 19: 74,
    20: 75, 21: 77, 22: 78, 23: 80, 24: 81, 25: 83, 26: 84, 27: 86, 28: 87, 29: 89,
    30: 90, 31: 91, 32: 93, 33: 94, 34: 96, 35: 98, 36: 99, 37: 101, 38: 102, 39: 104,
    40: 105, 41: 107, 42: 108, 43: 110, 44: 111, 45: 113, 46: 114, 47: 116, 48: 117, 49: 119,
    50: 121, 51: 122, 52: 124, 53: 125, 54: 127, 55: 128, 56: 130, 57: 132, 58: 133, 59: 135,
    60: 136, 61: 138, 62: 140, 63: 142, 64: 143, 65: 145, 66: 147, 67: 148, 68: 149, 69: 151,
    70: 152, 71: 156, 72: 157, 73: 157, 74: 159, 75: 162, 76: 163, 77: 164, 78: 165, 79: 167,
    80: 169, 81: 170, 82: 172, 83: 174, 84: 175, 85: 177, 86: 179, 87: 180, 88: 182, 89: 184,
    90: 185, 91: 187, 92: 188, 93: 190, 94: 192, 95: 193, 96: 195, 97: 197, 98: 199, 99: 200,
}

POSITION_WEIGHTS = {
    "Keepere": {
        "Håndtering": 19.23,
        "I luften": 19.23,
        "Spring": 19.23,
        "En mod en": 19.23,
        "Hurtighed": 7.69,
        "Acceleration": 7.69,
        "Aflevering": 1.92,
        "Udholdenhed": 1.92,
        "Kampånd": 1.92,
        "Lederskab": 1.92,
    },
    "Forsvar": {
        "Tackling": 40.54,
        "Hurtighed": 16.89,
        "Acceleration": 16.89,
        "Aflevering": 6.76,
        "Dribling": 3.38,
        "Udholdenhed": 3.38,
        "Kampånd": 3.38,
        "Dødboldsituationer": 3.38,
        "Lederskab": 3.38,
        "Afslutning": 2.03,
    },
    "Midtbane": {
        "Aflevering": 22.58,
        "Dribling": 19.35,
        "Udholdenhed": 16.13,
        "Afslutning": 12.90,
        "Hurtighed": 9.68,
        "Kampånd": 6.45,
        "Tackling": 3.23,
        "Acceleration": 3.23,
        "Dødboldsituationer": 3.23,
        "Lederskab": 3.23,
    },
    "Angreb": {
        "Afslutning": 30.03,
        "Dribling": 24.02,
        "Hurtighed": 15.02,
        "Acceleration": 15.02,
        "Aflevering": 3.0,
        "Kampånd": 3.0,
        "Dødboldsituationer": 3.0,
        "Lederskab": 3.0,
        "Udholdenhed": 2.4,
        "Tackling": 1.5,
    },
}

EXERCISE_STATS_BY_GROUP = {
    "Markspiller": {
        "Træningskamp": MARK_STATS,
        "Intervaltræning": ["Hurtighed", "Acceleration", "Udholdenhed"],
        "Team building": ["Udholdenhed", "Lederskab", "Kampånd"],
        "Dødboldsituationer": ["Acceleration", "Afslutning", "Dødboldsituationer"],
        "Teknik": ["Aflevering", "Afslutning", "Dribling", "Tackling", "Dødboldsituationer"],
        "Taktik": ["Aflevering", "Lederskab", "Tackling", "Dødboldsituationer"],
        "Én i midten": ["Acceleration", "Aflevering", "Udholdenhed", "Dribling"],
        "Offensiv træning": ["Hurtighed", "Kampånd", "Afslutning", "Dribling"],
        "Defensiv træning": ["Hurtighed", "Lederskab", "Kampånd", "Tackling"],
    },
    "Keepere": {
        "Træningskamp": GOALKEEPER_STATS,
        "Intervaltræning": ["Hurtighed", "Acceleration", "Udholdenhed"],
        "Team building": ["Udholdenhed", "Lederskab", "Kampånd"],
        "Dødboldsituationer": ["Acceleration", "En mod en", "I luften", "Spring"],
        "Teknik": ["Aflevering", "Håndtering", "En mod en", "I luften"],
        "Taktik": ["Aflevering", "Lederskab", "Kampånd", "En mod en"],
        "Håndtering": ["Hurtighed", "Aflevering", "Håndtering", "I luften"],
        "Fysisk træning": ["Hurtighed", "Udholdenhed", "Kampånd", "Spring"],
        "Indlæg": ["Acceleration", "Lederskab", "Håndtering", "Spring"],
    },
}
