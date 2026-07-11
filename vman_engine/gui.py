import copy
import csv
import json
import math
import textwrap
import threading
import queue
import time
import random
import io
import base64
import os
import subprocess
import sys
import tempfile
import webbrowser
import re
from urllib.parse import urljoin, urlsplit, urlunsplit
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import ttk, messagebox, filedialog, simpledialog

from .config import POSITION_STATS, POSITION_WEIGHTS
from .models import TrainingInstruction, TrainingCycle, PlayerState
from .rules import validate_distribution, exercises_for_position, normalize_position
from .simulator import simulate_program, summarize_state, calculate_end_age, calculate_rating
from .simulator import expand_program
from .optimizer import AssistantPlanResult, assistant_search_training_plans, assistant_search_training_plans_timed, search_time_to_seconds
from .player_fetcher import (
    fetch_player_snapshot, build_player_debug_bundle,
    engine_position_from_code, position_value_candidates,
)
import traceback
import unicodedata


def _enable_windows_dpi_awareness():
    """Make Windows render Tk widgets sharply on high-DPI displays.

    Without process DPI awareness, Windows may bitmap-scale the whole Tk window,
    which makes the app look softer/pixelated compared with macOS. This must run
    before the first Tk root window is created.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            return
        except Exception:
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return
        except Exception:
            pass
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    except Exception:
        pass


_enable_windows_dpi_awareness()


def _stabilize_windows_tk_scaling(root):
    """Keep the fixed-pixel Control Center layout stable at 100-150% Windows scaling.

    Tk normally converts point-sized fonts using the monitor DPI. The Control
    Center intentionally uses fixed pixel dimensions, so allowing Tk to enlarge
    fonts independently makes labels and controls overlap at 125%/150%. Use the
    standard 96-DPI Tk scale (96 / 72) on Windows so widgets retain the same
    proportions as the tested 100% layout. Windows still renders the process
    sharply because DPI awareness is enabled before the root window is created.
    """
    if os.name != "nt":
        return
    try:
        root.tk.call("tk", "scaling", 96.0 / 72.0)
    except Exception:
        pass


def _assistant_parallel_worker_entry(kwargs, progress_queue, worker_index):
    """ProcessPool entry point that can publish live progress from a worker.

    The callback is created inside the child process so it does not need to be
    pickled. Only the Manager queue and plain kwargs cross the process boundary.
    """
    kwargs = dict(kwargs or {})

    def worker_progress(info):
        if progress_queue is None:
            return
        try:
            payload = dict(info or {})
            payload["worker_index"] = int(worker_index)
            progress_queue.put(("progress", payload))
        except Exception:
            pass

    kwargs["progress_callback"] = worker_progress
    return assistant_search_training_plans_timed(**kwargs)


INTENSITY_OPTIONS = {
    "Afslappet": 20,
    "Normal": 21,
    "Intens": 22,
    "Hård": 23,
    "Ekstrem": 24,
}

INTENSITY_LABELS_BY_POINTS = {value: key for key, value in INTENSITY_OPTIONS.items()}

DEFAULT_APP_SETTINGS = {
    "language": "",
    "auto_template": True,
    "warn_overwrite": False,
    "autosave": True,
    "auto_backup": True,
    "cpu_threads": "Auto",
    "ui_scale": 1.0,
    "ui_text_overrides": {},
    "group_import_debug_dir": "",
}

LANGUAGE_LABELS = {
    "da": "Dansk",
    "en": "English",
}

LANGUAGE_CODES_BY_LABEL = {label: code for code, label in LANGUAGE_LABELS.items()}

CPU_THREAD_OPTIONS = ("Auto", "1 kerne", "2 kerner", "4 kerner", "Maks")



EBR_RATIO_MAX = 1.15

UI_TEXT_DA_TO_EN = {
    # Menu / common
    "Indstillinger": "Settings",
    "Hjælp": "Help",
    "Brugermanualen kunne ikke findes.": "The user manual could not be found.",
    "Kunne ikke åbne brugermanualen.": "Could not open the user manual.",
    "Donation": "Donation",
    "Om VMAN Training Planner": "About VMAN Training Planner",
    "Luk": "Close",
    "Gem": "Save",
    "Gem og luk": "Save and close",
    "Annuller": "Cancel",
    "Tilbage": "Back",
    "Næste": "Next",
    "Indlæs": "Load",
    "Ryd": "Clear",
    "Hent": "Fetch",
    "Tilføj": "Add",
    "Opdater": "Update",
    "Eksportér": "Export",
    "Slet valgte": "Delete selected",
    "Importer valgte til kontrolcenter": "Import selected to Control Center",
    "Importer til kontrolcenter": "Import to Control Center",
    "Rapport som PDF": "Report as PDF",
    "Rapport som DOC": "Report as DOC",
    "Historik som CSV": "History as CSV",
    "Gem søgning": "Save search",
    "Start simulator": "Start simulator",
    "Stop simulator": "Stop simulator",
    "Redigeringstilstand": "Edit mode",
    "UI-redigering": "UI editing",
    "Aktivér redigeringstilstand": "Enable edit mode",
    "Tekststørrelse": "Text size",
    "Nulstil størrelse": "Reset size",
    "Nulstil egne tekster": "Reset custom texts",
    "Dobbeltklik på en fast tekst i programmet for at omdøbe den.": "Double-click a fixed text in the app to rename it.",
    "Redigér tekst": "Edit text",
    "Oprindelig tekst": "Original text",
    "Ny tekst": "New text",
    "Gem tekst": "Save text",
    "Egne tekster nulstillet.": "Custom texts reset.",

    # Main UI
    "Spiller": "Player",
    "Position": "Position",
    "Startalder": "Start age",
    "Alder": "Age",
    "XP": "XP",
    "ved": "at",
    "Spillerlink": "Player link",
    "Importer:": "Import:",
    "Egenskaber": "Stats",
    "Vurderingstal": "Rating",
    "Egenskabssum": "Stat sum",
    "Session": "Session",
    "Dage": "Days",
    "Øvelse": "Exercise",
    "Intensitet": "Intensity",
    "Fordeling": "Distribution",
    "Point": "Points",
    "Træningsprogram": "Training program",
    "Resultat": "Result",
    "Erfaringsbonus": "Experience bonus",
    "Gradient": "Gradient",
    "Ratio": "Ratio",

    # Tables
    "VT": "Rating",
    "ΔVT": "ΔRating",
    "Gns.": "Avg.",
    "Metode": "Method",
    "Program": "Program",
    "Sessions": "Sessions",
    "Søgeresultater": "Search results",

    # Assistant
    "Assistent": "Assistant",
    "Assistent · 1/3 · Scenario": "Assistant · 1/3 · Scenario",
    "Assistent · 2/3 · Mål og begrænsninger": "Assistant · 2/3 · Goals and constraints",
    "Assistent · 3/3 · Søgestrategi": "Assistant · 3/3 · Search strategy",
    "Søgestrategi": "Search strategy",
    "Søgetid": "Search time",
    "Mål og begrænsninger": "Goals and constraints",
    "Scenarie": "Scenario",
    "Scenario": "Scenario",
    "Vægtning": "Weights",
    "Begrænsninger": "Constraints",
    "Autoskabelon": "Auto template",
    "Advarsel ved overskrivning": "Overwrite warning",
    "Autosave": "Autosave",
    "Automatisk gem backup": "Automatic backup",

    # Settings
    "Generelt": "General",
    "Advarsler": "Warnings",
    "Gem": "Save",
    "Ydeevne": "Performance",
    "Sprog": "Language",
    "Vælg Dansk eller English som programsprog.": "Choose Danish or English as the application language.",
    "CPU-kerner": "CPU cores",
    "CPU-kerner/tråde": "CPU cores",
    "Indstillinger gemt.": "Settings saved.",

    # About
    "Et uofficielt værktøj til at planlægge og simulere træning i Virtual Manager.\n\nUdviklet af Niklas Schmidt - FC Dronningemaen\nMed teknisk hjælp fra ChatGPT\n\nTak til alle der tester, finder fejl og kommer med idéer.\n\nVersion: 1.00\n\nVMAN Training Planner er ikke tilknyttet, godkendt af eller officielt forbundet med Virtual Manager.":
    "An unofficial tool for planning and simulating training in Virtual Manager.\n\nDeveloped by Niklas Schmidt - FC Dronningemaen\nWith technical help from ChatGPT\n\nThanks to everyone who tests, finds bugs and shares ideas.\n\nVersion: 1.00\n\nVMAN Training Planner is not affiliated with, endorsed by or officially connected to Virtual Manager.",
}

ADDITIONAL_UI_TEXT_DA_TO_EN = {
    # Domain labels
    "Egenskaber": "Abilities",
    "Vurdering": "Rating",
    "Vurderingstal": "Rating",
    "Gennemsnit": "Average",
    "Egenskabssum": "Total",
    "Stat sum": "Total",
    "Samlet": "Total",
    "Samlet:": "Total:",
    "dage": "days",
    "år": "years",
    "frem til": "until",
    "intensitet": "intensity",
    "bVT": "UW rating",

    # Control Center / program
    "Hent spiller": "Import player",
    "Hent": "Import",
    "spiller": "player",
    "gruppe": "group",
    "Ugyldigt link": "Invalid link",
    "Ingen spiller hentet": "No player imported",
    "Ingen stats endnu": "No abilities yet",
    "Avatar ikke hentet": "Avatar not imported",
    "Avatar ikke vist": "Avatar not shown",
    "Ingen avatar": "No avatar",
    "Vis graf": "Show graph",
    "Eksport": "Export",
    "Eksportér": "Export",
    "Gem som skabelon...": "Save as template...",
    "Indlæs skabelon fra fil...": "Load template from file...",
    "Tilføj til træningsprogram": "Add session",
    "Pointfordeling": "Point distribution",
    "Egenskab": "Ability",
    "Indsats": "Points",
    "Nulstil": "Reset",
    "Gruppeimport": "Group import",
    "Gruppe-rapport": "Group report",
    "Grupperapport": "Group report",
    "Indsæt spillerlinks eller klublink": "Paste player links or club link",
    "Hent liste": "Fetch list",
    "Vælg alle": "Select all",
    "Fravælg alle": "Select none",
    "Tilføj til": "Add to",
    "Genkald seneste søgning": "Recall latest search",
    "Ja": "Yes",
    "Nej": "No",
    "Seneste søgning genkaldt.": "Latest search recalled.",
    "Assistent-søgning ryddes": "Assistant search will be cleared",
    "Du har ændret i Spiller-sektionen. Den seneste Assistent-søgning ryddes, fordi den ikke længere passer til de aktuelle spillerdata.": "You changed the Player section. The latest Assistant search will be cleared because it no longer matches the current player data.",
    "Ændringen vil rydde Assistent-søgningen": "This change will clear the Assistant search",
    "Hvis du ændrer data i Spiller-sektionen, ryddes den aktuelle Assistent-søgning, fordi den ikke længere passer til spillerdataene.\n\nVil du fortsætte?": "If you change data in the Player section, the current Assistant search will be cleared because it no longer matches the player data.\n\nDo you want to continue?",
    "Assistenten er nulstillet efter ændringer i Spiller-sektionen.": "Assistant was reset after changes in the Player section.",
    "Aktiv gruppe": "Active group",
    "Medtag": "Use",
    "Navn": "Name",
    "Gruppenavn": "Group name",
    "Gennemsnitsgruppe": "Average group",
    "Debug gemt": "Debug saved",
    "Indsæt links på én linje": "Paste links on one line",
    "Antal": "Quantity",
    "Antal gentagelser": "Repetitions",
    "Opløs blok": "Dissolve cycle",
    "Kopiér": "Copy",
    "Indsæt": "Paste",
    "Duplikér": "Duplicate",
    "Redigér valgte": "Edit selected",
    "Kopiér valgte": "Copy selected",
    "Indsæt efter valgte": "Paste after selected",
    "Duplikér valgte": "Duplicate selected",
    "Flyt valgte op": "Move selected up",
    "Flyt valgte ned": "Move selected down",
    "Tilføj valgte til blok": "Add selected to cycle",
    "Slet": "Delete",
    "Blok": "Cycle",
    "Session": "Session",

    # Assistant
    "Mål og begrænsninger": "Goals and constraints",
    "Praktiske begrænsninger": "Practical constraints",
    "Længde på træningspas": "Session length",
    "Tillad intensitetsforøgelse": "Allow intensity boost",
    "Fast rul": "Fixed rotation",
    "bloklængde": "cycle length",
    "Træningskamp": "Training match",
    "Træningsperiode": "Training period",
    "Gruppér højtvægtede stats": "Group high-weighted abilities",
    "Straftolerance": "Penalty tolerance",
    "Vægtninger": "Weights",
    "Søg videre med": "Search further with",
    "Gensimulér med XP-variation": "Resimulate with XP variation",
    "ingen resultater endnu": "no results yet",
    "Højreklik på et resultat for at søge videre.": "Right-click a result to search further.",
    "Klar. Nye søgninger lægges oveni de gamle resultater.": "Ready. New searches are added to the existing results.",
    "Søgningen er ryddet. Justér side 1/2 og start en ny søgning.": "The search has been cleared. Adjust steps 1/2 and start a new search.",
    "Assistent-fejl": "Assistant error",
    "Assistenten kunne ikke gå videre.": "The Assistant could not continue.",
    "Ryd Assistent-søgning?": "Clear Assistant search?",
    "Ryd søgning": "Clear search",
    "Hvis du går tilbage fra side 3, ryddes den aktuelle søgning og søgeresultaterne.\n\nVil du fortsætte?": "If you go back from step 3, the current search and search results will be cleared.\n\nDo you want to continue?",

    # Settings descriptions
    "Skifter automatisk til den matchende Sheikh-skabelon ved positionsskift/spillerimport.": "Automatically switches to the matching Sheikh template when changing position/importing a player.",
    "Vis advarsel før handlinger der kan overskrive nuværende data:": "Show a warning before actions that can overwrite current data:",
    "• indlæsning af ny spiller": "• importing a new player",
    "• skift af skabelon": "• changing template",
    "• nulstilling af træningsprogram": "• resetting the training program",
    "• overskrivning af gemt træningsprogram eller Assistent-søgning": "• overwriting a saved training program or Assistant search",
    "Standard er slået fra.": "Off by default.",
    "Gemmer løbende seneste arbejdsstatus i en autosave-fil.": "Continuously saves the latest workspace state in an autosave file.",
    "Gemmer historiske backup-kopier ved ændringer. Bevares adskilt fra Autosave, så seneste tilstand og backup-historik kan bruges forskelligt.": "Saves historical backup copies when changes are made. Kept separate from Autosave so the latest state and backup history can be used differently.",
    "Antal CPU-kerner": "Number of CPU cores",
    "Antal CPU-kerner / tråde": "Number of CPU cores",
    "1 kerne": "1 core",
    "2 kerner": "2 cores",
    "4 kerner": "4 cores",
    "1 tråd": "1 core",
    "2 tråde": "2 cores",
    "4 tråde": "4 cores",
    "Maks": "Max",
    "Bruges aktivt ved alle Assistent-søgemetoder. Auto bruger alle tilgængelige CPU-kerner minus 2. Ved 2 eller flere valgte kerner reserveres én kerne til UI og løbende resultater.": "Used actively by all Assistant search methods. Auto uses all available CPU cores minus 2. When 2 or more cores are selected, one core is reserved for the UI and live results.",
    "Bruges aktivt ved Assistent-søgning, herunder Brute Force. Auto bruger alle tilgængelige CPU-kerner minus 2. Ved 2 eller flere valgte kerner reserveres én kerne til UI og løbende resultater.": "Used actively during Assistant searches, including Brute Force. Auto uses all available CPU cores minus 2. When 2 or more cores are selected, one core is reserved for the UI and live results.",
    "Bruges aktivt ved Assistent-søgning, herunder Brute Force. Auto bruger alle tilgængelige kerner/tråde minus 2, så computeren stadig har luft til UI og andre opgaver.": "Used actively during Assistant searches, including Brute Force. Auto uses all available CPU cores minus 2. When 2 or more cores are selected, one core is reserved for the UI and live results.",
    "Bekræft handling": "Confirm action",
    "kan overskrive nuværende data. Vil du fortsætte?": "can overwrite current data. Do you want to continue?",

    # Stats
    "Håndtering": "Handling",
    "I luften": "Aerial",
    "Spring": "Diving",
    "En mod en": "One on one",
    "Én mod én": "One on one",
    "Hurtighed": "Speed",
    "Aflevering": "Passing",
    "Udholdenhed": "Stamina",
    "Kampånd": "Perseverance",
    "Lederskab": "Leadership",
    "Afslutning": "Finishing",
    "Dødboldsituationer": "Set pieces",

    # Exercises / intensities / positions
    "Intervaltræning": "Interval training",
    "Teknik": "Technique",
    "Taktik": "Tactics",
    "Én i midten": "Pig in the middle",
    "Offensiv træning": "Offensive training",
    "Defensiv træning": "Defensive training",
    "Fysisk træning": "Physical training",
    "Indlæg": "Crossing",
    "Afslappet": "Very light",
    "Intens": "Intense",
    "Hård": "Hard",
    "Ekstrem": "Extreme",
    "Forsvar": "Defender",
    "Midtbane": "Midfielder",
    "Angreb": "Forward",

    # Search / tolerance options
    "Ingen": "None",
    "Lav": "Low",
    "Mellem": "Medium",
    "Høj": "High",
    "10 sek": "10 sec",
    "1 time": "1 hour",
    "4 timer": "4 hours",
    "8 timer": "8 hours",
    "Manuel": "Manual",
    "Søger videre": "Searching further",
    "Simulerer": "Simulating",
    "Stopper efter nuværende simulering…": "Stopping after current simulation…",
    "Stopper…": "Stopping…",
    "Færdig. Ingen resultater.": "Done. No results.",
    "Start simulator": "Start simulator",
    "Stop simulator": "Stop simulator",
    "Redigeringstilstand": "Edit mode",
    "UI-redigering": "UI editing",
    "Aktivér redigeringstilstand": "Enable edit mode",
    "Tekststørrelse": "Text size",
    "Nulstil størrelse": "Reset size",
    "Nulstil egne tekster": "Reset custom texts",
    "Dobbeltklik på en fast tekst i programmet for at omdøbe den.": "Double-click a fixed text in the app to rename it.",
    "Redigér tekst": "Edit text",
    "Oprindelig tekst": "Original text",
    "Ny tekst": "New text",
    "Gem tekst": "Save text",
    "Egne tekster nulstillet.": "Custom texts reset.",
    "bedste VT": "best rating",
    "samlet": "total",
    "tilbage": "remaining",
    "kører til Stop": "runs until Stop",
    "procesresultater": "process results",
    "udfald": "outcomes",
    "nye udfald": "new outcomes",
    "eksisterende resultater bevares": "existing results are kept",
    "indtil Stop simulator": "until Stop simulator",
    "Gruppe": "Group",
    "Tidsfrist": "Time limit",
    "Resultater": "Results",
    "Søgning": "Search",
    "Henter spiller…": "Importing player…",
    "Indsæt først et spillerlink.": "Paste a player link first.",
    "Gemmer debug-filer…": "Saving debug files…",
    "Debug-filer er gemt på din Mac.": "Debug files have been saved on your Mac.",
    "Åbn MobilePay-link": "Open MobilePay link",
    "Eksportér rapport": "Export report",
    "DOC-rapport gemt.": "DOC report saved.",
    "PDF-rapport gemt.": "PDF report saved.",
    "Gensimulér med XP-variation": "Resimulate with XP variation",
    "Søg videre": "Search further",
    "TM17 + Queen Seeker kan kun vælges som ny søgning, ikke som 'søg videre' fra et eksisterende program.": "TM17 + Queen Seeker can only be chosen as a new search, not as 'search further' from an existing program.",
    "Kunne ikke indlæse søgningen.": "Could not load the search.",
}
UI_TEXT_DA_TO_EN.update(ADDITIONAL_UI_TEXT_DA_TO_EN)
UI_TEXT_EN_TO_DA = {value: key for key, value in UI_TEXT_DA_TO_EN.items()}


STAT_DA_TO_EN = {
    "Håndtering": "Handling",
    "I luften": "Aerial",
    "Spring": "Diving",
    "En mod en": "One on one",
    "Hurtighed": "Speed",
    "Acceleration": "Acceleration",
    "Aflevering": "Passing",
    "Udholdenhed": "Stamina",
    "Kampånd": "Perseverance",
    "Lederskab": "Leadership",
    "Afslutning": "Finishing",
    "Dribling": "Dribbling",
    "Tackling": "Tackling",
    "Dødboldsituationer": "Set pieces",
}
STAT_EN_TO_DA = {value: key for key, value in STAT_DA_TO_EN.items()}

EXERCISE_DA_TO_EN = {
    "Træningskamp": "Training match",
    "Intervaltræning": "Interval training",
    "Team building": "Team building",
    "Dødboldsituationer": "Set pieces",
    "Teknik": "Technique",
    "Taktik": "Tactics",
    "Én i midten": "Pig in the middle",
    "Offensiv træning": "Offensive training",
    "Defensiv træning": "Defensive training",
    "Håndtering": "Handling",
    "Fysisk træning": "Physical training",
    "Indlæg": "Crossing",
}
EXERCISE_EN_TO_DA = {value: key for key, value in EXERCISE_DA_TO_EN.items()}

INTENSITY_DA_TO_EN = {
    "Afslappet": "Very light",
    "Normal": "Normal",
    "Intens": "Intense",
    "Hård": "Hard",
    "Ekstrem": "Extreme",
}
INTENSITY_EN_TO_DA = {value: key for key, value in INTENSITY_DA_TO_EN.items()}

POSITION_DA_TO_EN = {
    "Keeper": "Keeper",
    "Keepere": "Keeper",
    "Forsvar": "Defender",
    "Midtbane": "Midfielder",
    "Angreb": "Forward",
}
POSITION_EN_TO_DA = {
    "Keeper": "Keepere",
    "Defender": "Forsvar",
    "Midfielder": "Midtbane",
    "Forward": "Angreb",
}

SEARCH_TIME_DA_TO_EN = {
    "10 sek": "10 sec",
    "1 min": "1 min",
    "5 min": "5 min",
    "15 min": "15 min",
    "1 time": "1 hour",
    "4 timer": "4 hours",
    "8 timer": "8 hours",
    "Manuel": "Manual",
}
SEARCH_TIME_EN_TO_DA = {value: key for key, value in SEARCH_TIME_DA_TO_EN.items()}

PENALTY_TOLERANCE_DA_TO_EN = {
    "Ingen": "None",
    "Lav": "Low",
    "Mellem": "Medium",
    "Høj": "High",
}
PENALTY_TOLERANCE_EN_TO_DA = {value: key for key, value in PENALTY_TOLERANCE_DA_TO_EN.items()}

CPU_THREAD_DA_TO_EN = {
    "Auto": "Auto",
    "1 kerne": "1 core",
    "2 kerner": "2 cores",
    "4 kerner": "4 cores",
    "Maks": "Max",
}
CPU_THREAD_ALIASES = {
    "Auto": "Auto",
    "Maks": "Maks",
    "Max": "Maks",
    "1 kerne": "1 kerne",
    "2 kerner": "2 kerner",
    "4 kerner": "4 kerner",
    "1 core": "1 kerne",
    "2 cores": "2 kerner",
    "4 cores": "4 kerner",
    "1 tråd": "1 kerne",
    "2 tråde": "2 kerner",
    "4 tråde": "4 kerner",
    "1 thread": "1 kerne",
    "2 threads": "2 kerner",
    "4 threads": "4 kerner",
}
CPU_THREAD_EN_TO_DA = {value: key for key, value in CPU_THREAD_DA_TO_EN.items()}


def normalize_cpu_thread_setting(value):
    text = str(value or "Auto")
    return CPU_THREAD_ALIASES.get(text, text if text in CPU_THREAD_OPTIONS else "Auto")


def display_cpu_thread(value, language="da"):
    internal = normalize_cpu_thread_setting(value)
    return CPU_THREAD_DA_TO_EN.get(internal, internal) if language == "en" else internal


def internal_cpu_thread(value):
    return normalize_cpu_thread_setting(value)


BUILTIN_PRESETS = {
    "Sheikh_k": {'position': 'Keepere',
 'start_age': '15.0',
 'xp_at_hard_intensity': '180',
 'xp_reference_intensity': 'Hård',
 'experience_bonus': {'enabled': False, 'ratio': '1.10', 'knee': 'late'},
 'start_stats': {'Håndtering': '2',
                 'I luften': '2',
                 'Spring': '2',
                 'En mod en': '2',
                 'Hurtighed': '2',
                 'Acceleration': '2',
                 'Aflevering': '2',
                 'Udholdenhed': '2',
                 'Kampånd': '2',
                 'Lederskab': '2'},
 'program': [{'type': 'phase',
              'days': 90,
              'exercise': 'Træningskamp',
              'intensity': 'Hård',
              'training_points': 23,
              'distribution': None},
             {'type': 'block',
              'name': 'Blok 1',
              'repetitions': 30,
              'phases': [{'type': 'phase',
                          'days': 2,
                          'exercise': 'Dødboldsituationer',
                          'intensity': 'Hård',
                          'training_points': 23,
                          'distribution': {'Acceleration': 5, 'En mod en': 5, 'I luften': 5, 'Spring': 8}},
                         {'type': 'phase',
                          'days': 2,
                          'exercise': 'Teknik',
                          'intensity': 'Hård',
                          'training_points': 23,
                          'distribution': {'Aflevering': 3, 'Håndtering': 10, 'En mod en': 5, 'I luften': 5}},
                         {'type': 'phase',
                          'days': 1,
                          'exercise': 'Fysisk træning',
                          'intensity': 'Hård',
                          'training_points': 23,
                          'distribution': {'Hurtighed': 10, 'Udholdenhed': 5, 'Kampånd': 4, 'Spring': 4}},
                         {'type': 'phase',
                          'days': 2,
                          'exercise': 'Træningskamp',
                          'intensity': 'Hård',
                          'training_points': 23,
                          'distribution': None}]}]},
    "Sheikh_f": {'position': 'Forsvar',
 'start_age': '15.0',
 'xp_at_hard_intensity': '180',
 'xp_reference_intensity': 'Hård',
 'experience_bonus': {'enabled': False, 'ratio': '1.10', 'knee': 'late'},
 'start_stats': {'Hurtighed': '2',
                 'Acceleration': '2',
                 'Udholdenhed': '2',
                 'Aflevering': '2',
                 'Afslutning': '2',
                 'Dribling': '2',
                 'Tackling': '2',
                 'Dødboldsituationer': '2',
                 'Lederskab': '2',
                 'Kampånd': '2'},
 'program': [{'type': 'phase',
              'days': 90,
              'exercise': 'Træningskamp',
              'intensity': 'Hård',
              'training_points': 23,
              'distribution': None},
             {'type': 'block',
              'name': 'Blok 1',
              'repetitions': 30,
              'phases': [{'type': 'phase',
                          'days': 2,
                          'exercise': 'Defensiv træning',
                          'intensity': 'Hård',
                          'training_points': 23,
                          'distribution': {'Hurtighed': 9, 'Lederskab': 2, 'Kampånd': 2, 'Tackling': 10}},
                         {'type': 'phase',
                          'days': 2,
                          'exercise': 'Intervaltræning',
                          'intensity': 'Hård',
                          'training_points': 23,
                          'distribution': {'Hurtighed': 8, 'Acceleration': 8, 'Udholdenhed': 7}},
                         {'type': 'phase',
                          'days': 2,
                          'exercise': 'Én i midten',
                          'intensity': 'Hård',
                          'training_points': 23,
                          'distribution': {'Acceleration': 2, 'Aflevering': 10, 'Udholdenhed': 2, 'Dribling': 9}},
                         {'type': 'phase',
                          'days': 1,
                          'exercise': 'Træningskamp',
                          'intensity': 'Hård',
                          'training_points': 23,
                          'distribution': None}]}]},
    "Sheikh_m": {'position': 'Midtbane',
 'start_age': '15.0',
 'xp_at_hard_intensity': '180',
 'xp_reference_intensity': 'Hård',
 'experience_bonus': {'enabled': False, 'ratio': '1.10', 'knee': 'late'},
 'start_stats': {'Hurtighed': '2',
                 'Acceleration': '2',
                 'Udholdenhed': '2',
                 'Aflevering': '2',
                 'Afslutning': '2',
                 'Dribling': '2',
                 'Tackling': '2',
                 'Dødboldsituationer': '2',
                 'Lederskab': '2',
                 'Kampånd': '2'},
 'program': [{'type': 'phase',
              'days': 90,
              'exercise': 'Træningskamp',
              'intensity': 'Hård',
              'training_points': 23,
              'distribution': None},
             {'type': 'block',
              'name': 'Blok 1',
              'repetitions': 30,
              'phases': [{'type': 'phase',
                          'days': 2,
                          'exercise': 'Teknik',
                          'intensity': 'Hård',
                          'training_points': 23,
                          'distribution': {'Aflevering': 5,
                                           'Afslutning': 4,
                                           'Dribling': 4,
                                           'Tackling': 8,
                                           'Dødboldsituationer': 2}},
                         {'type': 'phase',
                          'days': 2,
                          'exercise': 'Offensiv træning',
                          'intensity': 'Hård',
                          'training_points': 23,
                          'distribution': {'Hurtighed': 8, 'Kampånd': 7, 'Afslutning': 6, 'Dribling': 2}},
                         {'type': 'phase',
                          'days': 2,
                          'exercise': 'Én i midten',
                          'intensity': 'Hård',
                          'training_points': 23,
                          'distribution': {'Acceleration': 4, 'Aflevering': 5, 'Udholdenhed': 10, 'Dribling': 4}},
                         {'type': 'phase',
                          'days': 1,
                          'exercise': 'Træningskamp',
                          'intensity': 'Hård',
                          'training_points': 23,
                          'distribution': None}]}]},
    "Sheikh_a": {'position': 'Angreb',
 'start_age': '15.0',
 'xp_at_hard_intensity': '180',
 'xp_reference_intensity': 'Hård',
 'experience_bonus': {'enabled': False, 'ratio': '1.10', 'knee': 'late'},
 'start_stats': {'Hurtighed': '2',
                 'Acceleration': '2',
                 'Udholdenhed': '2',
                 'Aflevering': '2',
                 'Afslutning': '2',
                 'Dribling': '2',
                 'Tackling': '2',
                 'Dødboldsituationer': '2',
                 'Lederskab': '2',
                 'Kampånd': '2'},
 'program': [{'type': 'phase',
              'days': 90,
              'exercise': 'Træningskamp',
              'intensity': 'Hård',
              'training_points': 23,
              'distribution': None},
             {'type': 'block',
              'name': 'Blok 1',
              'repetitions': 30,
              'phases': [{'type': 'phase',
                          'days': 2,
                          'exercise': 'Offensiv træning',
                          'intensity': 'Hård',
                          'training_points': 23,
                          'distribution': {'Hurtighed': 7, 'Kampånd': 5, 'Afslutning': 5, 'Dribling': 6}},
                         {'type': 'phase',
                          'days': 2,
                          'exercise': 'Teknik',
                          'intensity': 'Hård',
                          'training_points': 23,
                          'distribution': {'Aflevering': 5,
                                           'Afslutning': 6,
                                           'Dribling': 5,
                                           'Tackling': 5,
                                           'Dødboldsituationer': 2}},
                         {'type': 'phase',
                          'days': 2,
                          'exercise': 'Intervaltræning',
                          'intensity': 'Hård',
                          'training_points': 23,
                          'distribution': {'Hurtighed': 3, 'Acceleration': 10, 'Udholdenhed': 10}},
                         {'type': 'phase',
                          'days': 1,
                          'exercise': 'Træningskamp',
                          'intensity': 'Hård',
                          'training_points': 23,
                          'distribution': None}]}]},
}

DISPLAY_POSITIONS_DA = ["Keeper", "Forsvar", "Midtbane", "Angreb"]
DISPLAY_POSITIONS_EN = ["Keeper", "Defender", "Midfielder", "Forward"]
DISPLAY_POSITIONS = DISPLAY_POSITIONS_DA


def display_position(position, language="da"):
    normalized = normalize_position(POSITION_EN_TO_DA.get(str(position), str(position)))
    if language == "en":
        return POSITION_DA_TO_EN.get(normalized, "Keeper" if normalized == "Keepere" else normalized)
    return "Keeper" if normalized == "Keepere" else normalized


def internal_position(position):
    text = str(position)
    return normalize_position(POSITION_EN_TO_DA.get(text, text))


def display_stat(stat, language="da"):
    return STAT_DA_TO_EN.get(stat, stat) if language == "en" else STAT_EN_TO_DA.get(stat, stat)


def display_exercise(exercise, language="da"):
    return EXERCISE_DA_TO_EN.get(exercise, exercise) if language == "en" else EXERCISE_EN_TO_DA.get(exercise, exercise)


def internal_exercise(exercise):
    return EXERCISE_EN_TO_DA.get(str(exercise), str(exercise))


def display_intensity(intensity, language="da"):
    return INTENSITY_DA_TO_EN.get(intensity, intensity) if language == "en" else INTENSITY_EN_TO_DA.get(intensity, intensity)


def internal_intensity(intensity):
    return INTENSITY_EN_TO_DA.get(str(intensity), str(intensity))


def display_search_time(value, language="da"):
    return SEARCH_TIME_DA_TO_EN.get(value, value) if language == "en" else SEARCH_TIME_EN_TO_DA.get(value, value)


def internal_search_time(value):
    return SEARCH_TIME_EN_TO_DA.get(str(value), str(value))


def display_penalty_tolerance(value, language="da"):
    return PENALTY_TOLERANCE_DA_TO_EN.get(value, value) if language == "en" else PENALTY_TOLERANCE_EN_TO_DA.get(value, value)


def internal_penalty_tolerance(value):
    return PENALTY_TOLERANCE_EN_TO_DA.get(str(value), str(value))


class VmanApp(tk.Tk):
    def __init__(self):
        super().__init__()
        # Windows DPI fix: establish the same Tk text/widget scale as the
        # validated 100% layout before any styles or widgets are created.
        _stabilize_windows_tk_scaling(self)
        self.title("VMAN Training Planner 1.00")
        # Use the same application icon in Tk windows on both platforms.
        # The platform-specific .ico/.icns files are used by the build scripts.
        self._app_icon_photo = None
        try:
            icon_path = Path(__file__).resolve().parent / "assets" / "vman_training_planner.png"
            if icon_path.exists():
                self._app_icon_photo = tk.PhotoImage(file=str(icon_path))
                self.iconphoto(True, self._app_icon_photo)
        except (tk.TclError, OSError):
            self._app_icon_photo = None
        self._set_initial_window_geometry()
        # 4.50: Skjul hovedvinduet mens UI'et bygges, så brugeren ikke ser
        # widgets dukke op én efter én under opstart.
        self._startup_window_hidden = True
        try:
            self.withdraw()
        except Exception:
            pass

        self.program = []
        self.clipboard_phases = []
        self.last_history = []
        self.last_summary = None
        self.current_stats = POSITION_STATS["Keepere"]
        self.active_position = "Keepere"
        self._auto_after_id = None
        self.editing_index = None
        self.quantity_editor_updating = False
        self.quantity_after_id = None
        self.drag_source_index = None
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.drag_source_selection = []
        self.drag_mode = None
        self.drag_select_anchor_index = None
        self.drag_select_started = False
        self.drag_is_dragging = False
        self.drag_drop_index = None
        self.drag_ghost = None
        self.drag_ghost_label = None
        self.program_drop_indicator = None
        self.live_stats_summary = None
        self.live_stats_position = None
        self.selected_template_name_var = tk.StringVar(value="Sheikh_k")
        self.current_template_name = "Sheikh_k"
        self.template_dirty = False
        self._suppress_template_dirty = False
        self.assistant_window = None
        self.assistant_step = 0
        self.assistant_data = {}
        self._assistant_recall_snapshot = None
        self._assistant_reset_warning_shown = False
        self._assistant_resetting_from_main = False
        self._restoring_main_parameter_snapshot = False
        self._assistant_reset_prompt_active = False
        self._assistant_reset_prompt_cooldown = False
        self._main_parameter_last_snapshot = None
        self.undo_stack = []
        self.redo_stack = []
        self._undo_suspended = False
        self._restoring_undo = False
        self._ui_ready_for_undo = False
        self._last_main_snapshot = None
        self._player_link_raw_value = ""
        self._player_link_display_value = ""
        self._player_link_display_mode = "raw"
        self._player_link_group_import_after_id = None
        self._group_import_auto_open_blocked_link = ""
        self._group_import_fetch_generation = 0
        self._group_import_fetch_queue = None
        self._group_import_fetch_thread = None
        self._group_import_fetch_after_id = None
        self.group_import_window = None
        self.group_report_window = None
        self.group_import_players = []
        self.group_import_groups = []
        self.active_group = None
        self.group_report_history = []
        self._group_avatar_rotation_after_id = None
        self._group_avatar_rotation_generation = 0
        self._group_avatar_rotation_index = -1
        self._group_avatar_photo_cache = {}
        self._group_avatar_debug_records = []
        self._group_avatar_debug_summary = {}
        self._group_avatar_current_key = None
        self._main_stat_debug_events = []
        self._main_stat_last_values = {}
        self._group_report_program_source = ""
        self._session_technique_baseline_height = None
        self._session_technique_distribution_row_height = None
        self._session_baseline_calibrating = False
        self._session_interval_baseline_width = None
        self._session_width_calibrating = False
        # 2.32: Intervaltræningens længste egenskabsnavn er "Acceleration"
        # (12 tegn). Brug samme labelkolonne for alle øvelser, så indsats-
        # boksene ikke rykker horisontalt ved øvelsesskift.
        self._session_stat_label_width_chars = 12
        self._syncing_right_sections = False
        self.player_avatar_photo = None
        self.player_avatar_placeholder_photo = None
        self.player_avatar_source_url = None
        # 2.41: VMANs spillerportrætter er rektangulære (typisk 180×220).
        # Avatarområdet designes derfor som en portrætramme i samme forhold
        # i stedet for en kvadratisk ramme med beskæring.
        self._player_avatar_image_width = 90
        self._player_avatar_image_height = 110
        self._player_avatar_border = 1
        self._player_avatar_box_width = self._player_avatar_image_width + (2 * self._player_avatar_border)
        self._player_avatar_box_height = self._player_avatar_image_height + (2 * self._player_avatar_border)
        # 2.43: Samme synlige feltbredde til XP, startalder, startstats og dage.
        self._main_numeric_field_width = 4
        # 2.44: Kun skrift/labels får fælles startdefinition.
        # Redigeringsfelternes geometri beholdes som i 2.43.
        self._shared_label_padx = (0, 0)

        # 3.00: Globale indstillinger. De gemmes uden for projektmappen, så
        # brugerens valg bevares på tværs af nye builds.
        self.app_settings = self._load_app_settings()
        self.settings_window = None
        self.donation_window = None
        self.about_window = None
        self._donation_photo = None
        self._donation_photo_original = None
        self._settings_vars = {}
        self._autosave_after_id = None
        self._settings_runtime_ready = False
        # UI edit mode removed in 3.18. Keep heading keys for language refresh only.
        self._tree_heading_text_keys = {}

        self._configure_styles()
        self._layout_sync_enabled = False
        self._build_ui()
        self._on_position_change(initial=True)
        # 4.51: De gamle kalibreringer ændrede midlertidigt position/øvelse,
        # genbyggede Pointfordeling og kaldte update_idletasks flere gange.
        # Det var dyrt og gav også risiko for flash. Brug faste pixelkonstanter
        # fra det eksisterende Windows-layout i stedet.
        self._session_interval_baseline_width = 363
        self._session_technique_baseline_height = 0
        self._session_technique_distribution_row_height = 225
        self._apply_session_interval_baseline_width()
        self._apply_session_technique_layout_baseline()
        self._layout_sync_enabled = True
        self.after(80, self._sync_right_sections_with_left)
        self._load_builtin_preset("Sheikh_k", prompt_if_existing=False)
        self._apply_startup_avatar_and_name()
        self._ui_ready_for_undo = True
        self._sync_undo_baseline()
        self._settings_runtime_ready = True
        self._apply_language_to_current_ui()
        self._schedule_workspace_autosave()
        try:
            self.after_idle(self._store_main_parameter_snapshot)
        except Exception:
            pass
        self.after_idle(self._finish_startup_show)

    def _finish_startup_show(self):
        """Vis først hovedvinduet, når første layout/redraw er gennemført."""
        try:
            # Kør de layoutdele, der ellers kan nå at dukke op forskudt.
            self.update_idletasks()
            try:
                self._sync_right_sections_with_left()
            except Exception:
                pass
            try:
                self._sync_intensity_suffix_labels()
            except Exception:
                pass
            try:
                self._update_program_tree_column_separators()
            except Exception:
                pass
            try:
                self._position_main_section_title_overlays()
            except Exception:
                pass
            self.update_idletasks()

            if getattr(self, "_startup_window_hidden", False):
                try:
                    self.deiconify()
                except Exception:
                    pass
                try:
                    self.lift()
                except Exception:
                    pass
                self._startup_window_hidden = False

            # En ekstra sync efter vinduet er mappet; dette bør ikke konstruere
            # nye elementer, kun rette endelige pixelplaceringer.
            self.after(60, self._post_startup_visible_sync)
        finally:
            try:
                self.after_idle(self._maybe_prompt_language)
            except Exception:
                pass

    def _post_startup_visible_sync(self):
        try:
            self._sync_right_sections_with_left()
        except Exception:
            pass
        try:
            self._sync_intensity_suffix_labels()
        except Exception:
            pass
        try:
            self._update_program_tree_column_separators()
        except Exception:
            pass
        try:
            self._position_main_section_title_overlays()
        except Exception:
            pass

    # ---------- UI ----------

    def _set_initial_window_geometry(self):
        """Fit the initial window to the current Windows screen/work area.

        The old fixed 1320×860 geometry can open partly outside smaller Windows
        displays. Keep the preferred proportions, but clamp to the available
        screen so the full Control Center is visible from launch.
        """
        try:
            screen_w = int(self.winfo_screenwidth() or 1320)
            screen_h = int(self.winfo_screenheight() or 860)
            preferred_w = 1320
            preferred_h = 900
            margin_w = 48 if self._is_windows_ui() else 0
            margin_h = 92 if self._is_windows_ui() else 0
            width = min(preferred_w, max(1040, screen_w - margin_w))
            height = min(preferred_h, max(640, screen_h - margin_h))
            x = max(0, (screen_w - width) // 2)
            y = max(0, min(30, (screen_h - height) // 3))
            self.geometry(f"{width}x{height}+{x}+{y}")
            # 3.85: Fast vinduesstørrelse. Brugeren skal ikke kunne resize.
            self.minsize(width, height)
            self.maxsize(width, height)
            self.resizable(False, False)
        except Exception:
            self.geometry("1320x900")
            self.minsize(1320, 900)
            self.maxsize(1320, 900)
            self.resizable(False, False)


    def _install_rounded_button_style(self, style):
        """Install a slightly rounded dark button border where Tk supports image elements."""
        try:
            from PIL import Image, ImageDraw, ImageTk

            bg = "#4c5055"
            active = "#5a5f66"
            pressed = "#3f4348"
            outline = "#70757d"

            def rounded_photo(color, border_color):
                scale = 3
                w, h = 24 * scale, 14 * scale
                radius = 4 * scale
                img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)
                draw.rounded_rectangle(
                    (1 * scale, 1 * scale, w - 2 * scale, h - 2 * scale),
                    radius=radius,
                    fill=color,
                    outline=border_color,
                    width=1 * scale,
                )
                img = img.resize((24, 14), Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(img)

            normal_img = rounded_photo(bg, outline)
            active_img = rounded_photo(active, "#858b94")
            pressed_img = rounded_photo(pressed, "#555b62")
            self._rounded_button_images = [normal_img, active_img, pressed_img]

            element_name = "RoundedButton.border"
            try:
                if element_name not in style.element_names():
                    style.element_create(
                        element_name,
                        "image",
                        normal_img,
                        ("active", active_img),
                        ("pressed", pressed_img),
                        border=(6, 5, 6, 5),
                        sticky="nsew",
                    )
            except Exception:
                pass

            try:
                style.layout(
                    "TButton",
                    [
                        (
                            element_name,
                            {
                                "sticky": "nsew",
                                "children": [
                                    (
                                        "Button.padding",
                                        {
                                            "sticky": "nsew",
                                            "children": [("Button.label", {"sticky": "nsew"})],
                                        },
                                    )
                                ],
                            },
                        )
                    ],
                )
            except Exception:
                pass
        except Exception:
            pass

    def _configure_styles(self):
        style = ttk.Style(self)
        # 3.87: Alt med den gamle lyse grå baggrund skiftes til en mørk
        # kulgrå baggrund, jf. brugerens farvereference.
        self._theme_panel_bg = "#2f2b2b"
        self._theme_panel_fg = "#f0f0f0"
        self._theme_panel_border = "#474242"
        try:
            self.configure(bg=self._theme_panel_bg)
        except Exception:
            pass
        if self._is_windows_ui():
            try:
                if "clam" in style.theme_names():
                    style.theme_use("clam")
                elif "vista" in style.theme_names():
                    style.theme_use("vista")
            except Exception:
                pass
            try:
                for name in (
                    "TkDefaultFont",
                    "TkTextFont",
                    "TkMenuFont",
                    "TkHeadingFont",
                    "TkCaptionFont",
                    "TkSmallCaptionFont",
                    "TkIconFont",
                    "TkTooltipFont",
                ):
                    try:
                        tkfont.nametofont(name).configure(family="Segoe UI", size=9)
                    except Exception:
                        pass
            except Exception:
                pass
        try:
            style.configure("TFrame", background=self._theme_panel_bg)
            style.configure("TLabel", background=self._theme_panel_bg, foreground=self._theme_panel_fg)
            style.configure("TCheckbutton", background=self._theme_panel_bg, foreground=self._theme_panel_fg)
            style.map("TCheckbutton", background=[("active", self._theme_panel_bg)])
            style.configure("TLabelframe", background=self._theme_panel_bg, bordercolor=self._theme_panel_border)
            # 3.88: Gør udfyldningsfelter mørke som resten af kontrolcenteret.
            style.configure(
                "TEntry",
                fieldbackground=self._theme_panel_bg,
                foreground=self._theme_panel_fg,
                background=self._theme_panel_bg,
                insertcolor=self._theme_panel_fg,
            )
            for combostyle in ("TCombobox", "Dark.TCombobox"):
                style.configure(
                    combostyle,
                    fieldbackground=self._theme_panel_bg,
                    foreground=self._theme_panel_fg,
                    background=self._theme_panel_bg,
                    arrowcolor=self._theme_panel_fg,
                    bordercolor=self._theme_panel_border,
                    lightcolor=self._theme_panel_border,
                    darkcolor=self._theme_panel_border,
                    padding=(8, 0, 24, 0),
                    arrowsize=10,
                    relief="flat",
                )
                style.map(
                    combostyle,
                    fieldbackground=[("readonly", self._theme_panel_bg), ("disabled", self._theme_panel_bg), ("!disabled", self._theme_panel_bg)],
                    foreground=[("readonly", self._theme_panel_fg), ("disabled", "#cfcfcf"), ("!disabled", self._theme_panel_fg)],
                    background=[("readonly", self._theme_panel_bg), ("!disabled", self._theme_panel_bg)],
                    selectbackground=[("readonly", self._theme_panel_bg), ("!disabled", self._theme_panel_bg)],
                    selectforeground=[("readonly", self._theme_panel_fg), ("!disabled", self._theme_panel_fg)],
                    arrowcolor=[("readonly", self._theme_panel_fg), ("!disabled", self._theme_panel_fg)],
                )
            style.configure("TMenubutton", background=self._theme_panel_bg, foreground=self._theme_panel_fg)
            style.map("TMenubutton", background=[("active", self._theme_panel_bg)], foreground=[("active", self._theme_panel_fg)])
            # 4.16: Kun skabelon-dropdownen gøres en smule lavere.
            style.configure(
                "TemplateDropdown.TMenubutton",
                background=self._theme_panel_bg,
                foreground=self._theme_panel_fg,
                padding=(6, 3, 18, 3),
            )
            style.map(
                "TemplateDropdown.TMenubutton",
                background=[("active", self._theme_panel_bg)],
                foreground=[("active", self._theme_panel_fg)],
            )
            style.configure("Treeview", background=self._theme_panel_bg, fieldbackground=self._theme_panel_bg, foreground=self._theme_panel_fg)
            style.map("Treeview", background=[("selected", "#4a4444")], foreground=[("selected", "#ffffff")])
            style.configure(
                "Treeview.Heading",
                background=self._theme_panel_bg,
                foreground=self._theme_panel_fg,
                relief="flat",
                borderwidth=1,
            )
            style.map("Treeview.Heading", background=[("active", self._theme_panel_bg)], foreground=[("active", self._theme_panel_fg)])
            style.configure("Dark.Treeview", background=self._theme_panel_bg, fieldbackground=self._theme_panel_bg, foreground=self._theme_panel_fg)
            style.map("Dark.Treeview", background=[("selected", "#4a4444")], foreground=[("selected", "#ffffff")])
            style.configure("Dark.Treeview.Heading", background=self._theme_panel_bg, foreground=self._theme_panel_fg, relief="flat", borderwidth=1)
            style.map("Dark.Treeview.Heading", background=[("active", self._theme_panel_bg)], foreground=[("active", self._theme_panel_fg)])
            style.configure(
                "Program.Treeview.Heading",
                background=self._theme_panel_bg,
                foreground=self._theme_panel_fg,
                relief="flat",
                borderwidth=1,
                lightcolor=self._theme_panel_border,
                darkcolor=self._theme_panel_border,
                bordercolor=self._theme_panel_border,
            )
            style.map("Program.Treeview.Heading", background=[("active", self._theme_panel_bg)], foreground=[("active", self._theme_panel_fg)])
        except Exception:
            pass
        style.configure("TLabelframe.Label", font=("", 14, "bold"), background=self._theme_panel_bg, foreground=self._theme_panel_fg)
        # 3.75: Hovedsektionernes titler skal ligge tydeligere over
        # sektionsafgrænsningen på Windows. Brug en særskilt stil, så mindre
        # interne LabelFrames som Pointfordeling ikke flyttes med.
        style.configure("MainSection.TLabelframe.Label", font=("", 14, "bold"), padding=(2, 0, 0, 0), background=self._theme_panel_bg, foreground=self._theme_panel_fg)
        # 3.92: Giv standardknapperne et mere moderne, mørkt udtryk.
        style.configure(
            "TButton",
            padding=(14, 2),
            background="#4c5055",
            foreground="#f3f4f6",
            borderwidth=1,
            relief="flat",
            focusthickness=0,
            focuscolor=self._theme_panel_bg,
            lightcolor="#6b7077",
            darkcolor="#3b3f44",
            bordercolor="#6b7077",
        )
        style.map(
            "TButton",
            background=[("active", "#5a5f66"), ("pressed", "#3f4348"), ("disabled", "#3a3a3a")],
            foreground=[("disabled", "#b9bcc1")],
            bordercolor=[("active", "#7b8087"), ("pressed", "#3b3f44")],
            lightcolor=[("active", "#7b8087"), ("pressed", "#3b3f44")],
            darkcolor=[("active", "#44484d"), ("pressed", "#2f3236")],
        )
        self._install_rounded_button_style(style)
        style.configure("Assistant.TButton", font=("", 12, "bold"), padding=(16, 4), foreground="#ffffff", background="#2563eb")
        style.map("Assistant.TButton", background=[("active", "#1d4ed8"), ("pressed", "#1e40af")], foreground=[("disabled", "#e5e7eb")])
        style.configure("Undo.TButton", font=("", 16, "bold"), padding=(6, 0))

        # 2.20: Smal sort kant omkring Træningsprogram-tabellen.
        try:
            style.layout("Program.Treeview", style.layout("Treeview"))
        except Exception:
            pass
        style.configure(
            "Program.Treeview",
            background=self._theme_panel_bg,
            fieldbackground=self._theme_panel_bg,
            foreground=self._theme_panel_fg,
            borderwidth=1,
            relief="solid",
            lightcolor=self._theme_panel_border,
            darkcolor=self._theme_panel_border,
            bordercolor=self._theme_panel_border,
        )
        style.map("Program.Treeview", background=[("selected", "#4a4444")], foreground=[("selected", "#ffffff")])
        # 3.89: Sørg også for at dropdown-lister og ældre Tk-felter følger den mørke farve.
        try:
            self.option_add("*TCombobox*Listbox*Background", self._theme_panel_bg)
            self.option_add("*TCombobox*Listbox*Foreground", self._theme_panel_fg)
            self.option_add("*TCombobox*Listbox*selectBackground", "#4a4444")
            self.option_add("*TCombobox*Listbox*selectForeground", "#ffffff")
            self.option_add("*Listbox*Background", self._theme_panel_bg)
            self.option_add("*Listbox*Foreground", self._theme_panel_fg)
            self.option_add("*Entry*Background", self._theme_panel_bg)
            self.option_add("*Entry*Foreground", self._theme_panel_fg)
        except Exception:
            pass

    def _validate_numeric_input(self, proposed, integer=False, decimals=2, max_value=None):
        """Key-validation for numeric Entry/Spinbox fields.

        Empty intermediate input is allowed while typing. Otherwise, values may
        have at most `decimals` decimals. Integer fields accept only whole digits.
        Optional max_value blocks values above the hard limit.
        """
        value_text = str(proposed).strip()
        if value_text == "":
            return True

        if integer:
            if "." in value_text or "," in value_text:
                return False
            if not value_text.isdigit():
                return False
        else:
            if value_text.count(".") + value_text.count(",") > 1:
                return False
            allowed = set("0123456789.,")
            if any(ch not in allowed for ch in value_text):
                return False
            if "." in value_text or "," in value_text:
                sep = "." if "." in value_text else ","
                before, after = value_text.split(sep, 1)
                if before and not before.isdigit():
                    return False
                if after and not after.isdigit():
                    return False
                if len(after) > int(decimals):
                    return False
            elif not value_text.isdigit():
                return False

        normalized = value_text.replace(",", ".")
        if normalized in {".", ","}:
            return True

        try:
            numeric_value = float(normalized)
        except Exception:
            return True

        if max_value is not None and numeric_value > float(max_value):
            return False

        return True

    def _configure_numeric_widget(self, widget, integer=False, decimals=2, max_value=None):
        try:
            vcmd = (
                self.register(
                    lambda proposed, integer=integer, decimals=decimals, max_value=max_value:
                        self._validate_numeric_input(
                            proposed,
                            integer=integer,
                            decimals=decimals,
                            max_value=max_value,
                        )
                ),
                "%P",
            )
            widget.configure(validate="key", validatecommand=vcmd)
        except Exception:
            pass
        # 3.61: Tk Spinbox can auto-repeat if the arrow button remains logically
        # pressed. On macOS this showed up as a stat running from e.g. 3 to 100.
        # Keep one-click increment/decrement, but effectively disable held-click
        # repeat for numeric fields.
        try:
            if "Spinbox" in str(widget.winfo_class()):
                widget.configure(repeatdelay=100000000, repeatinterval=100000000)
        except Exception:
            pass
        # 3.88: Tk Spinbox-felter skal også følge den mørke baggrund.
        try:
            if "Spinbox" in str(widget.winfo_class()):
                widget.configure(
                    bg=getattr(self, "_theme_panel_bg", "#2f2b2b"),
                    fg=getattr(self, "_theme_panel_fg", "#f0f0f0"),
                    buttonbackground=getattr(self, "_theme_panel_bg", "#2f2b2b"),
                    readonlybackground=getattr(self, "_theme_panel_bg", "#2f2b2b"),
                    disabledbackground=getattr(self, "_theme_panel_bg", "#2f2b2b"),
                    insertbackground=getattr(self, "_theme_panel_fg", "#f0f0f0"),
                    highlightbackground=getattr(self, "_theme_panel_border", "#474242"),
                    highlightcolor=getattr(self, "_theme_panel_border", "#474242"),
                )
        except Exception:
            pass
        return widget

    def _xp_line_spacing(self):
        """Pixel spacing for the XP/intensity row, tuned separately per language.

        Values are pixel paddings: (left/right padding around at/ved),
        then gap before intensity/intensitet. Kept deliberately small because
        macOS Aqua widgets already include their own internal padding.
        """
        if self._language_code() == "en":
            # 3.25: English fine-tune: pull the controls after XP left,
            # pull the intensity dropdown closer to "at", and move the
            # suffix a touch left as requested.
            return (0, 0), 4
        # 3.25: Danish fine-tune: move only the dropdown one pixel left,
        # while keeping the suffix label visually fixed.
        return (3, 3), 2

    def _sync_xp_line_spacing(self):
        try:
            at_pad, suffix_gap = self._xp_line_spacing()
            for label in (
                getattr(self, "xp_at_label", None),
                getattr(self, "assistant_xp_at_label", None),
            ):
                if label is not None and label.winfo_exists() and label.winfo_manager() == "pack":
                    label.pack_configure(padx=at_pad)
            for label in (
                getattr(self, "xp_intensity_suffix_label", None),
                getattr(self, "assistant_xp_intensity_suffix_label", None),
            ):
                if label is not None and label.winfo_exists() and label.winfo_manager() == "pack":
                    label.pack_configure(padx=(suffix_gap, 0))
        except Exception:
            pass

    def _refresh_session_exercise_options(self, position=None, reset_if_invalid=True):
        """Refresh exercise dropdown options for the current main-window position."""
        try:
            if not hasattr(self, "exercise_box") or not hasattr(self, "exercise_var"):
                return
            if position is None:
                position = self.position_var.get() if hasattr(self, "position_var") else getattr(self, "active_position", "Keepere")
            position = internal_position(position)
            exercises = list(exercises_for_position(position).keys())
            current = internal_exercise(self.exercise_var.get())
            self.exercise_box.configure(values=[self._display_exercise(value) for value in exercises])
            if current not in exercises:
                current = "Træningskamp"
            displayed_current = self._display_exercise(current)
            # 3.72: Ved sprogswitch skal øvelsesfeltet altid omsættes til det
            # aktuelle visningssprog, også når den underliggende øvelse er den
            # samme.
            if reset_if_invalid or self.exercise_var.get() != displayed_current:
                self.exercise_var.set(displayed_current)
            # Same exercise name can exist for both keeper and outfielders,
            # but its allowed stats can differ, so refresh the point inputs too.
            self._refresh_distribution_inputs()
        except Exception:
            pass

    def _redraw_fixed_intensity_dropdown(self, widget):
        try:
            if widget is None or not widget.winfo_exists():
                return
            if getattr(widget, "_vman_redrawing", False):
                return
            widget._vman_redrawing = True
            value = str(widget._vman_variable.get() or "").strip()
            if not value:
                value = self._display_intensity("Hård")
            if value and getattr(widget, "_vman_lowercase_display", False):
                value = value[:1].lower() + value[1:]
            width = int(getattr(widget, "_vman_fixed_width", 88))
            height = int(getattr(widget, "_vman_fixed_height", 22))
            visual_key = (value, width, height)
            try:
                if int(widget.winfo_width() or 0) != width or int(widget.winfo_height() or 0) != height:
                    widget.configure(width=width, height=height)
            except Exception:
                widget.configure(width=width, height=height)
            if getattr(widget, "_vman_last_visual_key", None) == visual_key:
                return
            widget._vman_last_visual_key = visual_key
            widget.delete("all")
            bg = getattr(self, "_theme_panel_bg", "#2f2b2b")
            fg = getattr(self, "_theme_panel_fg", "#f0f0f0")
            border = "#9aa0a6"
            arrow_bg = "#3b3b3b"
            widget.create_rectangle(0, 0, width - 1, height - 1, outline=border, fill=bg, width=1)
            arrow_w = 15
            ax = width - arrow_w
            widget.create_rectangle(ax, 1, width - 2, height - 2, outline="", fill=arrow_bg)
            widget.create_line(ax, 2, ax, height - 3, fill="#6b7280")
            widget.create_text(7, height // 2, text=value, anchor="w", fill=fg, font=("Segoe UI", 9))
            cx = ax + arrow_w // 2
            cy = height // 2 + 1
            widget.create_polygon(cx - 4, cy - 2, cx + 4, cy - 2, cx, cy + 3, fill=fg, outline="")
        except Exception:
            pass
        finally:
            try:
                widget._vman_redrawing = False
            except Exception:
                pass

    def _set_fixed_intensity_dropdown(self, widget, value, callback=None):
        try:
            if widget is None or not widget.winfo_exists():
                return
            widget._vman_variable.set(value)
            self._redraw_fixed_intensity_dropdown(widget)
            self.after_idle(self._sync_intensity_suffix_labels)
            if callback is not None:
                callback()
        except Exception:
            pass

    def _popup_fixed_intensity_dropdown(self, widget, callback=None):
        menu = None
        try:
            menu = tk.Menu(widget, tearoff=0)
            for value in self._intensity_options():
                label = value
                if label and getattr(widget, "_vman_lowercase_menu", False):
                    label = label[:1].lower() + label[1:]
                menu.add_command(label=label, command=lambda v=value: self._set_fixed_intensity_dropdown(widget, v, callback))
            menu.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height() + 1)
        except Exception:
            pass
        finally:
            try:
                if menu is not None:
                    menu.grab_release()
            except Exception:
                pass

    def _create_fixed_intensity_dropdown(self, parent, variable, callback=None):
        widget = tk.Canvas(
            parent,
            width=88,
            height=22,
            bd=0,
            highlightthickness=0,
            bg=getattr(self, "_theme_panel_bg", "#2f2b2b"),
            cursor="arrow",
        )
        widget._vman_is_fixed_intensity_dropdown = True
        widget._vman_variable = variable
        widget._vman_fixed_width = 88
        widget._vman_fixed_height = 22
        widget._vman_lowercase_display = False
        widget._vman_lowercase_menu = False
        widget.get = lambda : widget._vman_variable.get()
        widget.bind("<Button-1>", lambda event: self._popup_fixed_intensity_dropdown(widget, callback))
        widget.bind("<Map>", lambda event: self.after_idle(self._sync_intensity_suffix_labels), add="+")
        widget.bind("<Configure>", lambda event: self.after_idle(self._sync_intensity_suffix_labels), add="+")
        def _schedule_intensity_redraw(w=widget):
            try:
                if getattr(w, "_vman_redraw_scheduled", False):
                    return
                w._vman_redraw_scheduled = True
                self.after_idle(lambda ww=w: (setattr(ww, "_vman_redraw_scheduled", False), self._redraw_fixed_intensity_dropdown(ww)))
            except Exception:
                pass
        variable.trace_add("write", lambda *_: _schedule_intensity_redraw())
        _schedule_intensity_redraw()
        self.after_idle(self._sync_intensity_suffix_labels)
        self.after(50, self._sync_intensity_suffix_labels)
        return widget

    def _redraw_fixed_dropdown(self, widget):
        try:
            if widget is None or not widget.winfo_exists():
                return
            if getattr(widget, "_vman_redrawing", False):
                return
            widget._vman_redrawing = True
            value = str(widget._vman_variable.get() or "").strip()
            if value and getattr(widget, "_vman_lowercase_display", False):
                value = value[:1].lower() + value[1:]
            width = int(getattr(widget, "_vman_fixed_width", 88))
            height = int(getattr(widget, "_vman_fixed_height", 22))
            state = str(getattr(widget, "_vman_state", "readonly") or "readonly")
            visual_key = (value, width, height, state)
            try:
                if int(widget.winfo_width() or 0) != width or int(widget.winfo_height() or 0) != height:
                    widget._vman_original_configure(width=width, height=height)
            except Exception:
                widget._vman_original_configure(width=width, height=height)
            if getattr(widget, "_vman_last_visual_key", None) == visual_key:
                return
            widget._vman_last_visual_key = visual_key
            widget.delete("all")
            bg = getattr(self, "_theme_panel_bg", "#2f2b2b")
            fg = getattr(self, "_theme_panel_fg", "#f0f0f0")
            if state == "disabled":
                fg = "#8d8f94"
            border = "#9aa0a6"
            arrow_bg = "#3b3b3b" if state != "disabled" else "#333333"
            widget.create_rectangle(0, 0, width - 1, height - 1, outline=border, fill=bg, width=1)
            arrow_w = 15
            ax = width - arrow_w
            widget.create_rectangle(ax, 1, width - 2, height - 2, outline="", fill=arrow_bg)
            widget.create_line(ax, 2, ax, height - 3, fill="#6b7280")
            widget.create_text(7, height // 2, text=value, anchor="w", fill=fg, font=("Segoe UI", 9))
            cx = ax + arrow_w // 2
            cy = height // 2 + 1
            widget.create_polygon(cx - 4, cy - 2, cx + 4, cy - 2, cx, cy + 3, fill=fg, outline="")
        except Exception:
            pass
        finally:
            try:
                widget._vman_redrawing = False
            except Exception:
                pass

    def _fire_fixed_dropdown_callbacks(self, widget):
        callbacks = list(getattr(widget, "_vman_virtual_select_callbacks", []) or [])
        for callback in callbacks:
            try:
                callback(None)
            except TypeError:
                try:
                    callback()
                except Exception:
                    pass
            except Exception:
                pass

    def _set_fixed_dropdown_value(self, widget, value):
        try:
            if widget is None or not widget.winfo_exists():
                return
            widget._vman_variable.set(value)
            self._redraw_fixed_dropdown(widget)
            self._fire_fixed_dropdown_callbacks(widget)
        except Exception:
            pass

    def _popup_fixed_dropdown(self, widget):
        menu = None
        try:
            if str(getattr(widget, "_vman_state", "readonly")) == "disabled":
                return
            values = list(getattr(widget, "_vman_values", []) or [])
            if not values:
                return
            menu = tk.Menu(widget, tearoff=0)
            for value in values:
                menu.add_command(label=value, command=lambda v=value: self._set_fixed_dropdown_value(widget, v))
            menu.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height() + 1)
        except Exception:
            pass
        finally:
            try:
                if menu is not None:
                    menu.grab_release()
            except Exception:
                pass

    def _create_fixed_dropdown(self, parent, variable, values=(), width_px=88, height_px=22):
        widget = tk.Canvas(parent, width=int(width_px), height=int(height_px), bd=0, highlightthickness=0, bg=getattr(self, "_theme_panel_bg", "#2f2b2b"), cursor="arrow")
        widget._vman_is_fixed_dropdown = True
        widget._vman_variable = variable
        widget._vman_values = list(values or [])
        widget._vman_state = "readonly"
        widget._vman_fixed_width = int(width_px)
        widget._vman_fixed_height = int(height_px)
        widget._vman_virtual_select_callbacks = []
        widget._vman_original_bind = widget.bind
        widget._vman_original_configure = widget.configure
        widget._vman_original_cget = widget.cget
        def _get():
            return widget._vman_variable.get()
        def _set(value):
            widget._vman_variable.set(value)
            self._redraw_fixed_dropdown(widget)
        def _configure(*args, **kwargs):
            if args:
                return widget._vman_original_configure(*args, **kwargs)
            if "values" in kwargs:
                widget._vman_values = list(kwargs.pop("values") or [])
            if "state" in kwargs:
                widget._vman_state = kwargs.pop("state")
            if "width" in kwargs:
                try:
                    widget._vman_fixed_width = int(kwargs.pop("width"))
                except Exception:
                    kwargs.pop("width", None)
            if "height" in kwargs:
                try:
                    widget._vman_fixed_height = int(kwargs.pop("height"))
                except Exception:
                    kwargs.pop("height", None)
            result = None
            if kwargs:
                result = widget._vman_original_configure(**kwargs)
            self._redraw_fixed_dropdown(widget)
            return result
        def _cget(option):
            if option == "values": return tuple(widget._vman_values)
            if option == "state": return widget._vman_state
            if option == "width": return widget._vman_fixed_width
            if option == "height": return widget._vman_fixed_height
            if option == "style": return "FixedDropdown"
            return widget._vman_original_cget(option)
        def _bind(sequence=None, func=None, add=None):
            if sequence == "<<ComboboxSelected>>":
                if func is not None:
                    widget._vman_virtual_select_callbacks.append(func)
                return ""
            return widget._vman_original_bind(sequence, func, add)
        widget.get = _get
        widget.set = _set
        widget.configure = _configure
        widget.config = _configure
        widget.cget = _cget
        widget.bind = _bind
        widget._vman_original_bind("<Button-1>", lambda event: self._popup_fixed_dropdown(widget))
        def _schedule_redraw(w=widget):
            try:
                if getattr(w, "_vman_redraw_scheduled", False):
                    return
                w._vman_redraw_scheduled = True
                self.after_idle(lambda ww=w: (setattr(ww, "_vman_redraw_scheduled", False), self._redraw_fixed_dropdown(ww)))
            except Exception:
                pass
        widget._vman_original_bind("<Configure>", lambda event: _schedule_redraw(), add="+")
        variable.trace_add("write", lambda *_: _schedule_redraw())
        _schedule_redraw()
        return widget

    def _place_intensity_suffix_label(self, label, combo, parent):
        """Place the intensity suffix after the combobox when it is not managed by pack/grid."""
        try:
            if label is None or combo is None or parent is None:
                return
            if not combo.winfo_exists() or not parent.winfo_exists():
                return
            # Assistant step 1 now packs the suffix directly in the XP row. Do not also
            # place() it relative to the surrounding panel, because that was the cause
            # of the label floating over the Position row on some Tk/macOS layouts.
            if label.winfo_manager() in {"pack", "grid"}:
                try:
                    label.configure(text=self._tr("intensitet"))
                except Exception:
                    pass
                return
            parent_x = parent.winfo_rootx()
            parent_y = parent.winfo_rooty()
            combo_width = combo.winfo_width()
            combo_height = combo.winfo_height()
            if getattr(combo, "_vman_is_fixed_intensity_dropdown", False):
                combo_width = max(combo_width, int(getattr(combo, "_vman_fixed_width", 88)))
                combo_height = max(combo_height, int(getattr(combo, "_vman_fixed_height", 22)))
            x = combo.winfo_rootx() - parent_x + combo_width + self._xp_line_spacing()[1]
            y = combo.winfo_rooty() - parent_y + max(0, (combo_height - label.winfo_reqheight()) // 2)
            label.configure(text=self._tr("intensitet"))
            label.place(x=x, y=y)
            label.lift()
        except Exception:
            pass

    def _sync_intensity_suffix_labels(self, event=None):
        self._sync_xp_line_spacing()
        # Control Center
        self._place_intensity_suffix_label(
            getattr(self, "xp_intensity_suffix_label", None),
            getattr(self, "xp_reference_intensity_box", None),
            getattr(self, "xp_intensity_suffix_parent", None),
        )
        # Assistant window, if open
        self._place_intensity_suffix_label(
            getattr(self, "assistant_xp_intensity_suffix_label", None),
            getattr(self, "assistant_xp_reference_intensity_box", None),
            getattr(self, "assistant_xp_intensity_suffix_parent", None),
        )

    def _show_player_link_raw(self):
        if not hasattr(self, "player_link_var"):
            return
        raw = getattr(self, "_player_link_raw_value", "") or ""
        current = str(self.player_link_var.get()).strip()
        if current and current not in {getattr(self, "_player_link_display_value", ""), "Ugyldigt link"}:
            raw = current
            self._player_link_raw_value = raw
        self._player_link_display_mode = "raw"
        self.player_link_var.set(raw)
        try:
            self.player_ref_entry.selection_clear()
        except Exception:
            pass

    def _show_player_link_display(self):
        if not hasattr(self, "player_link_var"):
            return
        current = str(self.player_link_var.get()).strip()

        # Hvis brugeren har skrevet noget nyt, mens feltet var aktivt, bevar det
        # som rå værdi, indtil der trykkes Hent spiller.
        if self._player_link_display_mode == "raw":
            self._player_link_raw_value = current

        display = getattr(self, "_player_link_display_value", "") or ""
        if display:
            self.player_link_var.set(display)
            self._player_link_display_mode = "display"
        else:
            self.player_link_var.set(self._player_link_raw_value)
            self._player_link_display_mode = "raw"

    def _current_player_link_reference(self):
        if not hasattr(self, "player_link_var"):
            return ""
        current = str(self.player_link_var.get()).strip()
        display = getattr(self, "_player_link_display_value", "") or ""
        if self._player_link_display_mode == "display" and display and current == display:
            return getattr(self, "_player_link_raw_value", "") or ""
        if current == "Ugyldigt link":
            return getattr(self, "_player_link_raw_value", "") or ""
        self._player_link_raw_value = current
        return current

    def _main_notice(self, text):
        """Vis Kontrolcenter-beskeder uden ekstra statuslinje."""
        msg = str(text or "")
        if msg == "Ugyldigt link" and hasattr(self, "player_link_var"):
            current = str(self.player_link_var.get()).strip()
            if current and current != "Ugyldigt link":
                self._player_link_raw_value = current
            self._player_link_display_value = "Ugyldigt link"
            self._player_link_display_mode = "display"
            self.player_link_var.set("Ugyldigt link")
            return
        try:
            print(msg)
        except Exception:
            pass

    def _main_snapshot(self):
        if not hasattr(self, "position_var"):
            return None
        try:
            position = internal_position(self.position_var.get())
        except Exception:
            position = getattr(self, "active_position", "Forsvar")

        start_stats = {}
        if hasattr(self, "start_stat_vars"):
            start_stats = {stat: var.get() for stat, var in self.start_stat_vars.items()}

        return {
            "position": position,
            "start_age": self.start_age_var.get() if hasattr(self, "start_age_var") else "15.0",
            "xp": self.xp_var.get() if hasattr(self, "xp_var") else "180",
            "xp_reference_intensity": internal_intensity(self.xp_reference_intensity_var.get()) if hasattr(self, "xp_reference_intensity_var") else "Hård",
            "ebr_enabled": bool(self.ebr_enabled_var.get()) if hasattr(self, "ebr_enabled_var") else False,
            "ebr_ratio": self.ebr_ratio_var.get() if hasattr(self, "ebr_ratio_var") else "1.10",
            "ebr_knee": self.ebr_knee_var.get() if hasattr(self, "ebr_knee_var") else "linear",
            "start_stats": start_stats,
            "program": copy.deepcopy(getattr(self, "program", [])),
            "clipboard_phases": copy.deepcopy(getattr(self, "clipboard_phases", [])),
            "template_name": getattr(self, "current_template_name", "Skabelon"),
            "template_dirty": bool(getattr(self, "template_dirty", False)),
            "player_link_raw_value": getattr(self, "_player_link_raw_value", ""),
            "player_link_display_value": getattr(self, "_player_link_display_value", ""),
            "player_link_display_mode": getattr(self, "_player_link_display_mode", "raw"),
        }

    def _snapshot_repr(self, snapshot):
        try:
            return repr(snapshot)
        except Exception:
            return str(snapshot)

    def _sync_undo_baseline(self):
        snapshot = self._main_snapshot()
        if snapshot is not None:
            self._last_main_snapshot = copy.deepcopy(snapshot)

    def _record_undo_change(self, label="Ændring"):
        if not getattr(self, "_ui_ready_for_undo", False):
            return
        if getattr(self, "_undo_suspended", False) or getattr(self, "_restoring_undo", False):
            self._sync_undo_baseline()
            return

        current = self._main_snapshot()
        if current is None:
            return

        previous = getattr(self, "_last_main_snapshot", None)
        if previous is None:
            self._last_main_snapshot = copy.deepcopy(current)
            return

        if self._snapshot_repr(previous) != self._snapshot_repr(current):
            self.undo_stack.append(copy.deepcopy(previous))
            self.redo_stack.clear()
            if len(self.undo_stack) > 80:
                self.undo_stack = self.undo_stack[-80:]
            self._last_main_snapshot = copy.deepcopy(current)
            self._update_undo_button_state()

    def _push_undo(self, label=""):
        if getattr(self, "_undo_suspended", False) or getattr(self, "_restoring_undo", False):
            return
        snapshot = self._main_snapshot()
        if snapshot is None:
            return
        self.undo_stack.append(copy.deepcopy(snapshot))
        self.redo_stack.clear()
        if len(self.undo_stack) > 80:
            self.undo_stack = self.undo_stack[-80:]
        self._update_undo_button_state()

    def _update_undo_button_state(self):
        undo_button = getattr(self, "undo_button", None)
        redo_button = getattr(self, "redo_button", None)
        try:
            if undo_button is not None:
                undo_button.configure(state=("normal" if self.undo_stack else "disabled"))
            if redo_button is not None:
                redo_button.configure(state=("normal" if self.redo_stack else "disabled"))
        except Exception:
            pass

    def _undo_main(self):
        if not self.undo_stack:
            return
        current = self._main_snapshot()
        snapshot = self.undo_stack.pop()
        if current is not None:
            self.redo_stack.append(copy.deepcopy(current))
            if len(self.redo_stack) > 80:
                self.redo_stack = self.redo_stack[-80:]
        self._restore_main_snapshot(snapshot)
        self._update_undo_button_state()

    def _redo_main(self):
        if not self.redo_stack:
            return
        current = self._main_snapshot()
        snapshot = self.redo_stack.pop()
        if current is not None:
            self.undo_stack.append(copy.deepcopy(current))
            if len(self.undo_stack) > 80:
                self.undo_stack = self.undo_stack[-80:]
        self._restore_main_snapshot(snapshot)
        self._update_undo_button_state()

    def _restore_main_snapshot(self, snapshot):
        self._restoring_undo = True
        self._undo_suspended = True
        old_suppress = getattr(self, "_suppress_template_dirty", False)
        self._suppress_template_dirty = True
        try:
            position = internal_position(snapshot.get("position", "Forsvar"))
            self.position_var.set(self._display_position(position))
            self.current_stats = POSITION_STATS[position]
            self._render_start_stats()

            self._set_main_start_age_decimal(snapshot.get("start_age", "15.0"))
            self.xp_var.set(str(snapshot.get("xp", "180")))
            self.xp_reference_intensity_var.set(self._display_intensity(internal_intensity(snapshot.get("xp_reference_intensity", "Hård"))))

            self.ebr_enabled_var.set(bool(snapshot.get("ebr_enabled", False)))
            self.ebr_ratio_var.set(str(snapshot.get("ebr_ratio", "1.10")))
            self.ebr_knee_var.set(str(snapshot.get("ebr_knee", "linear")))
            self._on_ebr_toggle()

            start_stats = snapshot.get("start_stats", {})
            for stat in self.current_stats:
                self.start_stat_vars[stat].set(str(start_stats.get(stat, "2")))

            exercises = list(exercises_for_position(position).keys())
            self.exercise_box.configure(values=[self._display_exercise(value) for value in exercises])
            if internal_exercise(self.exercise_var.get()) not in exercises:
                self.exercise_var.set(self._display_exercise("Træningskamp"))

            self.program = copy.deepcopy(snapshot.get("program", []))
            self.clipboard_phases = copy.deepcopy(snapshot.get("clipboard_phases", []))
            self.editing_index = None
            self.active_position = position

            self._refresh_distribution_inputs()
            self._refresh_program_tree()
            self._set_template_name(
                snapshot.get("template_name", "Skabelon"),
                dirty=bool(snapshot.get("template_dirty", False)),
            )
            self._player_link_raw_value = snapshot.get("player_link_raw_value", "")
            self._player_link_display_value = snapshot.get("player_link_display_value", "")
            self._player_link_display_mode = snapshot.get("player_link_display_mode", "raw")
            if hasattr(self, "player_link_var"):
                if self._player_link_display_mode == "display" and self._player_link_display_value:
                    self.player_link_var.set(self._player_link_display_value)
                else:
                    self.player_link_var.set(self._player_link_raw_value)
            self._settings_changed()
            self._sync_undo_baseline()
            self._main_notice("Fortrudt.")
        finally:
            self._suppress_template_dirty = old_suppress
            self._undo_suspended = False
            self._restoring_undo = False

    def _is_text_input_focus(self):
        try:
            widget = self.focus_get()
            if widget is None:
                return False
            cls = widget.winfo_class()
            return cls in {"Entry", "TEntry", "Spinbox", "TSpinbox", "Text", "Combobox", "TCombobox"}
        except Exception:
            return False

    def _handle_global_copy(self, event=None):
        if self._is_text_input_focus():
            return None
        return self._copy_selected_phase()

    def _handle_global_paste(self, event=None):
        if self._is_text_input_focus():
            return None
        return self._paste_after_selected_phase()

    def _handle_global_duplicate(self, event=None):
        if self._is_text_input_focus():
            return None
        return self._duplicate_selected_phase()


    def _vman_stat_display_order(self, stats):
        """Vis stats i samme todelte rækkefølge som VMANs egenskabspanel."""
        vman_mark_order = [
            "Aflevering",
            "Afslutning",
            "Dribling",
            "Tackling",
            "Dødboldsituationer",
            "Hurtighed",
            "Acceleration",
            "Udholdenhed",
            "Lederskab",
            "Kampånd",
        ]
        vman_keeper_order = [
            "Håndtering",
            "En mod en",
            "Spring",
            "I luften",
            "Aflevering",
            "Hurtighed",
            "Acceleration",
            "Udholdenhed",
            "Lederskab",
            "Kampånd",
        ]

        stats = list(stats or [])
        stat_set = set(stats)

        if stat_set == set(vman_mark_order):
            return [stat for stat in vman_mark_order if stat in stats]

        if stat_set == set(vman_keeper_order):
            return [stat for stat in vman_keeper_order if stat in stats]

        return stats

    def _apply_startup_avatar_and_name(self):
        """Use bundled startup avatar + display name on app launch only.

        Ryd/Nulstil skal stadig gå tilbage til den almindelige generiske avatar,
        så denne metode kaldes kun ved opstart.
        """
        try:
            startup_name = "Anders Andersen"
            self._player_link_raw_value = ""
            self._player_link_display_value = startup_name
            self._player_link_display_mode = "display"
            if hasattr(self, "player_link_var"):
                self.player_link_var.set(startup_name)

            avatar_path = Path(__file__).resolve().parent / "assets" / "startup_avatar_anders.png"
            if avatar_path.exists():
                try:
                    image_bytes = avatar_path.read_bytes()
                    photo = self._make_avatar_photo(image_bytes, target_height=int(getattr(self, "_player_avatar_image_height", 110)))
                    if photo is not None:
                        self.player_avatar_photo = photo
                        self.player_avatar_source_url = None
                        self._place_player_avatar_photo(photo)
                        return
                except Exception:
                    pass
            self._set_player_avatar_placeholder()
        except Exception:
            pass

    def _make_generic_avatar_photo(self):
        """Lav et neutralt spillerportræt til tom/før-import-tilstand."""
        width = int(getattr(self, "_player_avatar_image_width", 90))
        height = int(getattr(self, "_player_avatar_image_height", 110))

        # 2.48: Brug det brugerleverede referenceikon som placeholder-avatar.
        # Billedet er præskaleret til avatarens dimensioner og kan derfor vises
        # robust direkte i Tk uden yderligere billedbehandling.
        try:
            from pathlib import Path
            avatar_path = Path(__file__).resolve().parent / "assets" / "generic_avatar.png"
            if avatar_path.exists():
                return tk.PhotoImage(file=str(avatar_path))
        except Exception:
            pass

        # Sidste fallback: en enkel neutral flade, så avatarfeltet aldrig er tomt.
        try:
            photo = tk.PhotoImage(width=width, height=height)
            photo.put("#ececec", to=(0, 0, width, height))
            photo.put("#111111", to=(int(width * 0.22), int(height * 0.14), int(width * 0.78), int(height * 0.30)))
            photo.put("#111111", to=(int(width * 0.26), int(height * 0.28), int(width * 0.74), int(height * 0.74)))
            photo.put("#f4f4f4", to=(int(width * 0.31), int(height * 0.33), int(width * 0.69), int(height * 0.73)))
            return photo
        except Exception:
            return None

    def _activate_player_link_entry(self, event=None):
        """Gør spillerlink-feltet nemt at redigere med ét klik.

        2.57: Lad Entry-widgetten selv håndtere museklik/cursorplacering.
        Vi skifter kun fra visningsnavn til råt link ved fokus, så klik ikke
        bliver "slugt" af en manuel focus/cursor-manøvre.
        """
        self._show_player_link_raw()
        return None

    def _on_player_link_var_changed(self, *_):
        """Åbn Gruppeimport automatisk, når hovedfeltet får et holdlink."""
        try:
            if getattr(self, "_player_link_display_mode", "raw") == "display":
                return
            raw = str(self.player_link_var.get() if hasattr(self, "player_link_var") else "").strip()
            normalized = self._normalize_group_input_url(raw)
            if not re.search(r"/clubs/\d+", normalized, flags=re.IGNORECASE):
                self._group_import_auto_open_blocked_link = ""
                return
            if normalized == getattr(self, "_group_import_auto_open_blocked_link", ""):
                return
            after_id = getattr(self, "_player_link_group_import_after_id", None)
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except Exception:
                    pass
            self._player_link_group_import_after_id = self.after(350, lambda: self._open_group_import_from_main_link(normalized))
        except Exception:
            pass

    def _open_group_import_from_main_link(self, link):
        try:
            self._player_link_group_import_after_id = None
            raw = str(link or "").strip()
            if not raw:
                return
            self._group_import_auto_open_blocked_link = ""
            self._show_group_import_window()
            if hasattr(self, "group_import_input_var"):
                self.group_import_input_var.set(raw)
            self._player_link_raw_value = raw
            self._player_link_display_mode = "raw"
            self._group_import_fetch_after_id = self.after(80, self._group_import_fetch)
        except Exception as error:
            self._main_notice(str(error))

    def _draw_avatar_photo_on_canvas(self, canvas, photo):
        """Tegn avatarfoto og ramme deterministisk på canvas.

        macOS/Tk viser ikke altid alle sider af en Frame-relief ens. Derfor
        tegnes rammen eksplicit oven på billedet, så højre/bund også fremgår.
        4.04: Hold altid en fast ramme og centrér billedet inde i den.
        """
        if canvas is None:
            return
        border = int(getattr(self, "_player_avatar_border", 1))
        box_width = int(getattr(self, "_player_avatar_box_width", 92))
        box_height = int(getattr(self, "_player_avatar_box_height", 112))
        image_width = int(getattr(self, "_player_avatar_image_width", 90))
        image_height = int(getattr(self, "_player_avatar_image_height", 110))
        try:
            if photo is not None:
                image_width = int(photo.width())
                image_height = int(photo.height())
        except Exception:
            pass
        try:
            canvas.configure(width=box_width, height=box_height)
            canvas.delete("all")
            canvas.create_rectangle(0, 0, box_width - 1, box_height - 1, outline="#ffffff", width=1)
            if photo is not None:
                # 4.05: Pixel-præcis placering: billedet starter direkte
                # inden for den tegnede ramme. Ingen dynamisk centrering og
                # ingen ekstern Tk-highlight-ramme.
                x = border
                y = border
                canvas.create_image(x, y, image=photo, anchor="nw")
                canvas.create_rectangle(0, 0, box_width - 1, box_height - 1, outline="#ffffff", width=1)
                canvas._avatar_photo = photo
        except Exception:
            pass

    def _place_player_avatar_photo(self, photo):
        if photo is None:
            return
        canvas = getattr(self, "player_avatar_canvas", None)
        if canvas is not None:
            self._draw_avatar_photo_on_canvas(canvas, photo)
            return
        if not hasattr(self, "player_avatar_label"):
            return
        try:
            image_width = int(photo.width())
            image_height = int(photo.height())
            border = int(getattr(self, "_player_avatar_border", 1))
            self.player_avatar_box.configure(
                width=image_width + (2 * border),
                height=image_height + (2 * border),
            )
            self.player_avatar_label.place(x=border, y=border, width=image_width, height=image_height)
        except Exception:
            pass
        self.player_avatar_label.configure(image=photo, text="", padx=0, pady=0, borderwidth=0, highlightthickness=0)

    def _set_player_avatar_placeholder(self, text=""):
        if not hasattr(self, "player_avatar_label"):
            return
        self.player_avatar_source_url = None
        photo = self._make_generic_avatar_photo()
        self.player_avatar_placeholder_photo = photo
        self.player_avatar_photo = photo
        if photo is not None:
            self._place_player_avatar_photo(photo)
        else:
            canvas = getattr(self, "player_avatar_canvas", None)
            if canvas is not None:
                self._draw_avatar_photo_on_canvas(canvas, None)
            else:
                # Sidste fallback: synlig tom portrætflade i stedet for at feltet forsvinder.
                self.player_avatar_label.configure(
                    image="",
                    text=text or "",
                    bg="#e6ebf0",
                    padx=0,
                    pady=0,
                    borderwidth=0,
                    highlightthickness=0,
                )

    def _avatar_input_extension(self, data):
        if data.startswith(b"\xff\xd8"):
            return ".jpg"
        if data.startswith(b"\x89PNG"):
            return ".png"
        if data[:6] in {b"GIF87a", b"GIF89a"}:
            return ".gif"
        if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
            return ".webp"
        return ".img"

    def _make_avatar_photo_with_pillow(self, data, target_height):
        from PIL import Image, ImageTk
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGBA")
            # 4.04: Skaler portrættet proportionalt, men altid inden for den
            # faste avatarboks, så intet bliver spist af rammen.
            width, height = img.size
            if width <= 0 or height <= 0:
                return None
            border = int(getattr(self, "_player_avatar_border", 1))
            max_width = int(getattr(self, "_player_avatar_box_width", 92)) - (2 * border)
            max_height = int(getattr(self, "_player_avatar_box_height", 112)) - (2 * border)
            if max_width <= 0 or max_height <= 0:
                return None
            scale = min(max_width / float(width), max_height / float(height))
            scale = max(scale, 0.01)
            target_width = max(1, int(round(width * scale)))
            target_height = max(1, int(round(height * scale)))
            img = img.resize((target_width, target_height), Image.LANCZOS)
            return ImageTk.PhotoImage(img)

    def _make_avatar_photo_with_tk(self, data):
        # Tk kan typisk læse PNG/GIF direkte, men ikke JPG på mange macOS-installationer.
        encoded = base64.b64encode(data).decode("ascii")
        return tk.PhotoImage(data=encoded)

    def _make_avatar_photo_with_macos_sips(self, data, target_width, target_height):
        # 2.36/2.41: Mac-fallback uden Pillow. VMAN-portrætter er JPG, og Tk
        # kan ofte ikke vise JPG direkte. macOS har normalt /usr/bin/sips, som
        # kan konvertere billedet til PNG i den ønskede portrætstørrelse.
        sips = "/usr/bin/sips"
        if not (os.path.exists(sips) and os.access(sips, os.X_OK)):
            return None
        in_path = out_path = None
        try:
            suffix = self._avatar_input_extension(data)
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as fh:
                fh.write(data)
                in_path = fh.name
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as fh:
                out_path = fh.name
            subprocess.run(
                [
                    sips,
                    "-s",
                    "format",
                    "png",
                    "-z",
                    str(int(target_height)),
                    str(int(target_width)),
                    in_path,
                    "--out",
                    out_path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=True,
            )
            return tk.PhotoImage(file=out_path)
        except Exception:
            return None
        finally:
            for path in (in_path, out_path):
                if path:
                    try:
                        os.unlink(path)
                    except Exception:
                        pass

    def _make_avatar_photo(self, image_bytes, target_height=None):
        if not image_bytes:
            return None
        data = bytes(image_bytes)
        target_height = int(target_height or getattr(self, "_player_avatar_image_height", 110))
        fallback_width = int(getattr(self, "_player_avatar_image_width", 90))

        # Førstevalg: Pillow, fordi VMANs portrætter typisk er JPG og fordi
        # Pillow lader os bevare billedets naturlige proportioner.
        try:
            return self._make_avatar_photo_with_pillow(data, target_height)
        except Exception:
            pass

        # Fallback for PNG/GIF, hvis Pillow ikke er installeret.
        try:
            return self._make_avatar_photo_with_tk(data)
        except Exception:
            pass

        # Mac-fallback: konvertér JPG til PNG via sips i VMANs portrætformat.
        try:
            return self._make_avatar_photo_with_macos_sips(data, fallback_width, target_height)
        except Exception:
            return None

    def _set_player_avatar_from_snapshot(self, snap):
        if not hasattr(self, "player_avatar_label"):
            return
        image_bytes = getattr(snap, "avatar_image_bytes", None)
        avatar_url = getattr(snap, "avatar_url", None)
        photo = self._make_avatar_photo(image_bytes, target_height=int(getattr(self, "_player_avatar_image_height", 110)))
        if photo is None:
            if avatar_url and image_bytes:
                self._set_player_avatar_placeholder("Avatar\nikke vist")
            elif avatar_url:
                self._set_player_avatar_placeholder("Avatar\nikke hentet")
            else:
                self._set_player_avatar_placeholder("Ingen\navatar")
            return
        self.player_avatar_photo = photo
        self.player_avatar_source_url = avatar_url
        self._place_player_avatar_photo(photo)

    def _format_imported_training_xp(self, value):
        try:
            numeric = int(float(value) + 0.5)
        except Exception:
            return None
        return str(max(0, min(999, numeric)))

    def _player_link_summary_text(self, snap):
        # Spillerlink-feltet/status skal kun vise spillernavn.
        # Importeret trænings-XP sættes stadig i XP-feltet, men vises ikke her.
        name = str(getattr(snap, "navn", "") or "").strip() or "Ukendt"
        return name

    def _reset_main_workspace(self):
        """Nulstil Kontrolcenter til en tom starttilstand."""
        if not self._confirm_overwrite_action("Nulstilling af træningsprogram"):
            return
        self._push_undo("Nulstil")
        old_suppress = getattr(self, "_suppress_template_dirty", False)
        self._undo_suspended = True
        self._suppress_template_dirty = True
        try:
            position = "Keepere"
            self.position_var.set(self._display_position(position))
            self.current_stats = POSITION_STATS[position]
            self._render_start_stats()

            self._set_main_start_age_decimal("15.0")
            self.xp_var.set("180")
            self.xp_reference_intensity_var.set(self._display_intensity("Hård"))

            self.ebr_enabled_var.set(False)
            self.ebr_ratio_var.set("1.10")
            self.ebr_knee_var.set("linear")
            self._on_ebr_toggle()

            for stat in self.current_stats:
                self.start_stat_vars[stat].set("2")

            self._player_link_raw_value = ""
            self._player_link_display_value = ""
            self._player_link_display_mode = "raw"
            self.active_group = None
            self._stop_group_avatar_rotation()
            self.player_link_var.set("")
            self._set_player_avatar_placeholder()
            self._set_group_report_button_visible(False)

            self.program = []
            self.clipboard_phases = []
            self.editing_index = None
            self.active_position = position

            exercises = list(exercises_for_position(position).keys())
            self.exercise_box.configure(values=[self._display_exercise(value) for value in exercises])
            self.days_var.set("2")
            self.intensity_var.set(self._display_intensity("Hård"))
            self.exercise_var.set(self._display_exercise("Træningskamp"))
            self._refresh_distribution_inputs()
            self._refresh_program_tree()
            self._clear_selection_and_editor()
            self.days_var.set("2")
            self._set_template_name("Skabelon", dirty=False)
            self._settings_changed()
            self._sync_undo_baseline()
        finally:
            self._suppress_template_dirty = old_suppress
            self._undo_suspended = False
            self._update_undo_button_state()

    def _fetch_player_link_to_main(self):
        reference = self._current_player_link_reference()
        if not reference:
            self._main_notice("Ugyldigt link")
            return

        # 3.57: Hvis spillerlinkfeltet indeholder et hold-/klublink, skal det
        # ikke behandles som én spiller. Åbn Gruppeimport og hent holdlisten der.
        try:
            normalized_reference = self._normalize_group_input_url(reference)
        except Exception:
            normalized_reference = str(reference or "")
        if re.search(r"/clubs/\d+", normalized_reference, flags=re.IGNORECASE):
            self._open_group_import_from_main_link(normalized_reference)
            return

        if not self._confirm_overwrite_action("Indlæsning af ny spiller"):
            return
        try:
            snap = fetch_player_snapshot(reference)

            self._push_undo("Hent spiller")
            self._undo_suspended = True
            try:
                fetched_position = internal_position(snap.engine_position)
                self.position_var.set(self._display_position(fetched_position))

                # 2.60: Når positionen kommer fra spillerlink, skal den matchende
                # Sheikh-skabelon følge med. _on_position_change() gør det kun ved
                # manuel positionsændring, fordi importen er undo-suspended.
                preset_name = self._sheikh_template_for_position(fetched_position)
                if self._setting_bool("auto_template", True) and preset_name and preset_name in BUILTIN_PRESETS:
                    self._load_builtin_preset(preset_name, prompt_if_existing=False)
                else:
                    self._on_position_change(initial=False)

                if snap.alder is not None:
                    self._set_main_start_age_decimal(snap.alder)

                imported_xp = self._format_imported_training_xp(getattr(snap, "training_xp_average", None))
                if imported_xp is not None:
                    self.xp_var.set(imported_xp)

                self._set_player_avatar_from_snapshot(snap)

                imported_stats = getattr(snap, "stats", None) or {}
                for stat, value in imported_stats.items():
                    if stat in getattr(self, "start_stat_vars", {}):
                        self.start_stat_vars[stat].set(str(int(round(float(value)))))

                self.active_group = None
                self._stop_group_avatar_rotation()
                self._set_group_report_button_visible(False)
                self._player_link_raw_value = reference
                self._player_link_display_value = self._player_link_summary_text(snap)
                self._player_link_display_mode = "display"
                self.player_link_var.set(self._player_link_display_value)

                self._settings_changed()
            finally:
                self._undo_suspended = False
        except Exception:
            self._player_link_raw_value = reference
            self._set_player_avatar_placeholder()
            self._main_notice("Ugyldigt link")


    # ---------- Group import ----------

    def _group_text(self, da, en):
        return en if self._language_code() == "en" else da

    def _set_group_report_button_visible(self, visible):
        btn = getattr(self, "group_report_button", None)
        if btn is None:
            return
        try:
            if bool(visible):
                try:
                    btn.pack_info()
                    return
                except Exception:
                    pass
                try:
                    btn.pack(side="right", padx=(0, 6), before=getattr(self, "graph_button", None))
                except Exception:
                    btn.pack(side="right", padx=(0, 6))
            else:
                btn.pack_forget()
        except Exception:
            pass

    def _snapshot_to_group_player(self, snap, source=""):
        stats = dict(getattr(snap, "stats", None) or {})
        player = {
            "selected": True,
            "name": str(getattr(snap, "navn", "") or "Ukendt"),
            "age": getattr(snap, "alder", None),
            "position_code": str(getattr(snap, "position_code", "") or ""),
            "engine_position": internal_position(getattr(snap, "engine_position", "Forsvar")),
            "rating": getattr(snap, "rating", None),
            "xp": getattr(snap, "training_xp_average", None),
            "url": str(getattr(snap, "url", "") or source or ""),
            "stats": stats,
            "avatar_url": getattr(snap, "avatar_url", None),
            "avatar_image_bytes": getattr(snap, "avatar_image_bytes", None),
            "avatar_content_type": getattr(snap, "avatar_content_type", None),
        }
        # VMAN pages do not always expose VT in a stable way. Group import should
        # still show a rating, so fall back to calculating it from the parsed
        # abilities when the page rating is missing.
        if player.get("rating") is None:
            player["rating"] = self._group_player_start_rating(player, player.get("engine_position"))
        return player

    def _group_player_start_rating(self, player, position=None):
        """Calculate the precise VMAN rating from imported ability values.

        Group list data can contain a rounded rating value, while the ability
        values are sufficient to reproduce the decimal VT shown by the player
        importer. For group reports we therefore prefer this calculated value
        whenever all required abilities for the player position are available.
        """
        position = internal_position(position or player.get("engine_position") or self.position_var.get())
        stats = player.get("stats") or {}
        if not stats:
            return None
        try:
            required = list(POSITION_STATS[position])
            if any(stat not in stats for stat in required):
                return None
            int_stats = {stat: int(round(float(stats[stat]))) for stat in required}
            state = PlayerState(stats=int_stats, stat_names=required)
            return float(calculate_rating(state, position, stat_mode="fractional"))
        except Exception:
            return None

    def _group_player_start_average(self, player, position=None):
        position = internal_position(position or player.get("engine_position") or self.position_var.get())
        stats = POSITION_STATS.get(position, [])
        pstats = player.get("stats") or {}
        values = []
        for stat in stats:
            try:
                if stat in pstats:
                    values.append(float(pstats.get(stat)))
            except Exception:
                pass
        if not values:
            return None
        return sum(values) / len(values)

    def _group_report_baseline_rows(self, group):
        """Return report rows where final values equal the imported start values.

        This is used when no training program or Assistant result has been selected yet.
        The report should still be meaningful instead of showing blank final columns.
        """
        rows = []
        try:
            target_position = internal_position(group.get("position", "Forsvar"))
        except Exception:
            target_position = "Forsvar"
        seen = set()
        for player in group.get("players", []) or []:
            key = self._group_player_key(player)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            try:
                player_position = internal_position(player.get("engine_position", target_position))
            except Exception:
                player_position = target_position
            start_rating = self._group_player_start_rating(player, player_position)
            if start_rating is None:
                start_rating = player.get("rating")
            try:
                start_rating = float(start_rating) if start_rating is not None else None
            except Exception:
                start_rating = None
            start_average = self._group_player_start_average(player, player_position)
            try:
                start_age = float(player.get("age")) if player.get("age") is not None else None
            except Exception:
                start_age = None
            rows.append({
                "key": key,
                "url": player.get("url", ""),
                "name": player.get("name", "Ukendt"),
                "start_age": start_age,
                "final_age": start_age,
                "position": player_position,
                "target_position": target_position,
                "start_rating": start_rating,
                "final_rating": start_rating,
                "delta": None if start_rating is None else 0.0,
                "average": start_average,
                "baseline": True,
            })
        return rows

    def _group_format_num(self, value, digits=2):
        if value is None:
            return "-"
        try:
            return f"{float(value):.{digits}f}"
        except Exception:
            return "-"

    def _group_report_format_num(self, value, digits=2):
        """Fixed-decimal display for Group report columns/exports.

        Group import intentionally uses compact numbers, but the group report
        must keep aligned decimal columns for start age, end age, start rating,
        final rating and averages. This helper is deliberately separate so
        future group-import display changes cannot accidentally remove report
        decimals again.
        """
        if value is None:
            return "-"
        try:
            return f"{float(str(value).replace(',', '.')):.{digits}f}"
        except Exception:
            return "-"

    def _group_import_format_num(self, value, digits=2):
        """Compact number display for Group import rows.

        Group import is a quick overview, so imported whole-number values should
        not be padded with .00. Non-integers are still shown with the requested
        precision. Group report keeps using _group_format_num because report
        columns need fixed decimal alignment.
        """
        if value is None:
            return "-"
        try:
            num = float(str(value).replace(",", "."))
            if abs(num - round(num)) < 0.005:
                return str(int(round(num)))
            return f"{num:.{digits}f}"
        except Exception:
            return "-"

    def _normalize_group_input_url(self, raw):
        raw = str(raw or "").strip()
        if raw.startswith("www."):
            raw = "https://" + raw
        elif raw.startswith("virtualmanager.com"):
            raw = "https://" + raw
        return raw

    def _choose_group_debug_dir(self):
        try:
            initial = str(self.app_settings.get("group_import_debug_dir", "") or Path.home())
            chosen = filedialog.askdirectory(
                title=self._group_text("Vælg mappe til gruppeimport-debug", "Choose folder for group import debug"),
                initialdir=initial if Path(initial).exists() else str(Path.home()),
                parent=getattr(self, "group_import_window", self),
            )
            if chosen:
                self.app_settings["group_import_debug_dir"] = chosen
                self._save_app_settings()
                if hasattr(self, "group_import_status_var"):
                    self.group_import_status_var.set(self._group_text(f"Debugmappe: {chosen}", f"Debug folder: {chosen}"))
                return Path(chosen)
        except Exception as error:
            if hasattr(self, "group_import_status_var"):
                self.group_import_status_var.set(str(error))
        return None

    def _group_debug_dir(self):
        override = str(getattr(self, "app_settings", {}).get("group_import_debug_dir", "") or "").strip()
        if override:
            try:
                base = Path(override).expanduser()
                base.mkdir(parents=True, exist_ok=True)
                return base
            except Exception:
                pass

        chosen = self._choose_group_debug_dir()
        if chosen is not None:
            return chosen

        try:
            base = self._settings_dir() / "debug"
            base.mkdir(parents=True, exist_ok=True)
            return base
        except Exception:
            base = Path(tempfile.gettempdir()) / "vman_engine_debug"
            base.mkdir(parents=True, exist_ok=True)
            return base

    def _write_group_import_debug(self, club_id, attempts, html_by_url, refs=None, note=""):
        try:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            debug_dir = self._group_debug_dir()
            report_path = debug_dir / f"group_import_club_{club_id or 'unknown'}_{stamp}.txt"
            lines = []
            lines.append("VMAN Training Planner gruppeimport-debug")
            lines.append("================================")
            lines.append(f"Tid: {stamp}")
            lines.append(f"Klub-id: {club_id or '-'}")
            if note:
                lines.append(f"Note: {note}")
            lines.append("")
            lines.append("Forsøg:")
            for attempt in attempts or []:
                lines.append(
                    f"- status={attempt.get('status')} ok={attempt.get('ok')} bytes={attempt.get('bytes')} "
                    f"url={attempt.get('url')} final={attempt.get('final_url')} error={attempt.get('error') or ''}"
                )
            lines.append("")
            lines.append(f"Fundne spillerlinks: {len(refs or [])}")
            for ref in (refs or [])[:120]:
                lines.append(f"- {ref}")
            report_path.write_text("\n".join(lines) + "\n", encoding="utf-8", errors="replace")
            for index, (url, html) in enumerate((html_by_url or {}).items(), start=1):
                safe_url = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(url))[:90]
                (debug_dir / f"group_import_club_{club_id or 'unknown'}_{stamp}_{index}_{safe_url}.html").write_text(
                    str(html or ""), encoding="utf-8", errors="replace"
                )
            return report_path
        except Exception:
            return None

    def _extract_club_player_ids_from_players_data(self, html):
        """Extract player ids from VMAN's embedded window.playersData JSON."""
        ids = []
        seen = set()
        try:
            raw = str(html or "").replace(r"\/", "/")
            decoder = json.JSONDecoder()
            for m in re.finditer(r"window\.playersData\s*=\s*", raw):
                tail = raw[m.end():].lstrip()
                try:
                    data, _ = decoder.raw_decode(tail)
                except Exception:
                    continue
                players = data.get("players", []) if isinstance(data, dict) else []
                if not isinstance(players, list):
                    continue
                for player in players:
                    if not isinstance(player, dict):
                        continue
                    pid = str(player.get("id", "")).strip()
                    if pid.isdigit() and pid not in seen:
                        seen.add(pid)
                        ids.append(pid)
        except Exception:
            pass
        return ids

    def _group_engine_position_from_vman_code(self, code):
        """Map positions from VMAN's club-list ``playersData`` source.

        This source does not use the same shorthand convention as the player
        profile parser in every case. In club-list data, F/FC/FV/FH can mean
        Forward, while defenders are exposed as D/DC/DL/DR. The group importer
        must therefore keep its own source-aware mapping instead of delegating
        blindly to the profile-code resolver, where F means Forsvar.

        Both A* and F* are accepted as attack codes because VMAN has exposed
        both variants in club-list data. Explicit position names win over a
        conflicting shorthand value in structured payloads.
        """
        candidates = position_value_candidates(code)

        # Prefer explicit names when the payload contains both a code and a
        # readable label, e.g. {"code": "F", "name": "Forward"}.
        for raw in candidates:
            text = re.sub(r"[_-]+", " ", str(raw).casefold())
            if re.search(r"\b(goalkeeper|goal keeper|keeper|målmand|maalmand)\b", text):
                return "Keepere"
            if re.search(r"\b(defender|defence|defense|forsvar|back|centre back|center back|full back|wing back)\b", text):
                return "Forsvar"
            if re.search(r"\b(midfielder|midfield|midtbane|central midfielder|defensive midfielder|attacking midfielder|offensive midfielder)\b", text):
                return "Midtbane"
            if re.search(r"\b(forward|attacker|striker|angriber|angreb|centre forward|center forward|winger)\b", text):
                return "Angreb"

        for raw in candidates:
            compact = re.sub(r"[^A-Z0-9]", "", str(raw).upper())
            if not compact:
                continue
            if compact in {"K", "GK", "G"} or compact.startswith("GK"):
                return "Keepere"
            if compact.startswith("D"):
                return "Forsvar"
            if compact.startswith("M"):
                return "Midtbane"
            if compact.startswith("A"):
                return "Angreb"
            if compact.startswith("F"):
                return "Angreb"

        return "Forsvar"

    def _extract_group_players_from_players_data(self, html, base_url="https://www.virtualmanager.com/"):
        """Parse VMAN club-page window.playersData directly into group-import rows.

        Club pages expose all visible players in window.playersData with names,
        age, position, rating and the new_* ability values. Using that data is
        more reliable than first extracting IDs and then opening every profile,
        because profile URLs without slugs can fail while the club list already
        contains the data we need for group averages.
        """
        out = []
        seen = set()
        stat_keys = {
            "Hurtighed": "new_speed",
            "Acceleration": "new_acceleration",
            "Udholdenhed": "new_stamina",
            "Aflevering": "new_passing",
            "Afslutning": "new_finishing",
            "Dribling": "new_dribbling",
            "Tackling": "new_tackling",
            "Dødboldsituationer": "new_set_pieces",
            "Lederskab": "new_leadership",
            "Kampånd": "new_perseverance",
            "Håndtering": "new_handling",
            "En mod en": "new_one_on_one",
            "I luften": "new_aerial",
            "Spring": "new_diving",
        }

        def as_float(value):
            try:
                if value is None or value == "":
                    return None
                return float(str(value).replace(",", "."))
            except Exception:
                return None

        try:
            raw = str(html or "").replace(r"\/", "/")
            decoder = json.JSONDecoder()
            for m in re.finditer(r"window\.playersData\s*=\s*", raw):
                tail = raw[m.end():].lstrip()
                try:
                    data, _ = decoder.raw_decode(tail)
                except Exception:
                    continue
                players = data.get("players", []) if isinstance(data, dict) else []
                if not isinstance(players, list):
                    continue
                for item in players:
                    if not isinstance(item, dict):
                        continue
                    pid = str(item.get("id", "")).strip()
                    if not pid or pid in seen:
                        continue
                    seen.add(pid)
                    # VMAN has exposed position as both a plain code and
                    # structured/named fields. Feed every available variant to
                    # the shared resolver and keep the first readable value for
                    # diagnostics/display.
                    position_payload = []
                    for position_key in (
                        "position_code", "positionCode", "position_short", "positionShort",
                        "position_abbreviation", "positionAbbreviation", "position",
                        "position_name", "positionName", "position_label", "positionLabel",
                        "role",
                    ):
                        if position_key in item:
                            position_payload.append(item.get(position_key))
                    candidates = position_value_candidates(position_payload)
                    code = candidates[0] if candidates else ""
                    engine_position = self._group_engine_position_from_vman_code(position_payload)
                    stats = {}
                    for stat, key in stat_keys.items():
                        value = as_float(item.get(key))
                        if value is not None:
                            stats[stat] = round(value, 2)
                    listed_rating = as_float(item.get("rating"))
                    rating = None
                    try:
                        required = list(POSITION_STATS[engine_position])
                        if all(stat in stats for stat in required):
                            state = PlayerState(
                                stats={stat: int(round(float(stats[stat]))) for stat in required},
                                stat_names=required,
                            )
                            rating = float(calculate_rating(state, engine_position, stat_mode="fractional"))
                    except Exception:
                        rating = None
                    if rating is None:
                        rating = listed_rating
                    name = str(item.get("name") or f"Player {pid}")
                    try:
                        slug = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
                        slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug.lower()).strip("-")
                    except Exception:
                        slug = ""
                    suffix = f"{pid}-{slug}" if slug else pid
                    out.append({
                        "name": name,
                        "age": as_float(item.get("age")),
                        "position": code or "?",
                        "engine_position": engine_position,
                        "rating": rating,
                        "xp": None,
                        "stats": stats,
                        "url": urljoin(base_url or "https://www.virtualmanager.com/", f"/da/players/{suffix}"),
                        "source": "club_players_data",
                        "assigned_group": "",
                        "avatar_url": None,
                        "avatar_image_bytes": None,
                        "avatar_content_type": None,
                    })
        except Exception:
            pass
        return out

    def _fetch_group_player_xp_only(self, url):
        """Fetch only the training-XP average for group import.

        Club imports only know the player id + generated slug. VMAN sometimes
        rejects the training page when the slug is not exact, so this tries the
        direct training URL first and then resolves the canonical profile URL as
        a fallback before opening /training.
        """
        try:
            import requests
            from .player_fetcher import (
                USER_AGENT,
                REQUEST_TIMEOUT,
                fetch_training_html_from_reference,
                summarize_training_xp_from_html,
                player_url_candidates,
            )
            session = requests.Session()
            session.headers.update({
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "da-DK,da;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": "https://www.virtualmanager.com/",
            })

            def parse_average(training_html):
                summary = summarize_training_xp_from_html(training_html)
                value = summary.get("average")
                return float(value) if value is not None else None

            # 1) Fast path: direct /training candidates from the known URL.
            try:
                _training_url, training_html = fetch_training_html_from_reference(url, session)
                value = parse_average(training_html)
                if value is not None:
                    return value
            except Exception:
                pass

            # 2) Resolve profile URL first, then use the final/canonical URL for training.
            profile_candidates = []
            try:
                profile_candidates.extend(player_url_candidates(url))
            except Exception:
                profile_candidates.append(str(url or ""))
            seen = set()
            for profile_url in profile_candidates:
                if not profile_url or profile_url in seen:
                    continue
                seen.add(profile_url)
                try:
                    resp = session.get(profile_url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
                    resp.raise_for_status()
                    html = resp.text or ""
                    candidates = [getattr(resp, "url", profile_url) or profile_url]
                    for pat in [
                        r"<link[^>]+rel=[\"\']canonical[\"\'][^>]+href=[\"\']([^\"\']+)",
                        r"<meta[^>]+property=[\"\']og:url[\"\'][^>]+content=[\"\']([^\"\']+)",
                    ]:
                        for match in re.finditer(pat, html, flags=re.IGNORECASE):
                            candidates.append(urljoin(profile_url, match.group(1)))
                    for resolved in candidates:
                        try:
                            _training_url, training_html = fetch_training_html_from_reference(resolved, session)
                            value = parse_average(training_html)
                            if value is not None:
                                return value
                        except Exception:
                            continue
                except Exception:
                    continue
            return None
        except Exception:
            return None

    def _fetch_group_player_profile_enrichment(self, player):
        """Fetch precise profile data for a Group import player.

        The club-list window.playersData is fast and gives us all visible
        players, but on VMAN it may only expose whole-number age/rating values.
        Group report needs the same precise start age and start-VT that the
        single-player importer can read from the player profile. This helper is
        used in the existing background fetch, so the UI still appears quickly
        and then updates as precise data arrives.
        """
        updates = {}
        url = str((player or {}).get("url", "") or "").strip()
        if not url:
            return updates

        # The club-list position is already decoded with the source-specific
        # playersData mapping. Profile enrichment is only allowed to add precise
        # age, VT/stat and XP data; it must not replace that known-good position
        # with a profile parser result that may use another shorthand convention
        # or fall back to Forsvar when no profile position is found.
        club_list_source = str((player or {}).get("source", "") or "") == "club_players_data"
        authoritative_group_position = internal_position(
            (player or {}).get("engine_position", "Forsvar")
        )
        try:
            snap = fetch_player_snapshot(url)
            try:
                name = str(getattr(snap, "navn", "") or "").strip()
                if name and name != "Ukendt":
                    updates["name"] = name
            except Exception:
                pass
            try:
                age = getattr(snap, "alder", None)
                if age is not None:
                    updates["age"] = float(age)
            except Exception:
                pass
            try:
                rating = getattr(snap, "rating", None)
                if rating is not None:
                    updates["rating"] = float(rating)
            except Exception:
                pass
            try:
                xp = getattr(snap, "training_xp_average", None)
                if xp is not None:
                    updates["xp"] = float(xp)
            except Exception:
                pass
            try:
                stats = dict(getattr(snap, "stats", None) or {})
                if stats:
                    updates["stats"] = stats
            except Exception:
                pass
            if not club_list_source:
                try:
                    position_code = str(getattr(snap, "position_code", "") or "").strip()
                    if position_code:
                        updates["position_code"] = position_code
                except Exception:
                    pass
                try:
                    engine_position = getattr(snap, "engine_position", None)
                    if engine_position:
                        updates["engine_position"] = internal_position(engine_position)
                except Exception:
                    pass
            try:
                # Prefer the reproducible VT from the precise profile ability
                # values over any rounded rating text/list value. This is the
                # value the group report needs for decimal Start-VT.
                calc_position = (
                    authoritative_group_position
                    if club_list_source
                    else internal_position(
                        updates.get("engine_position")
                        or getattr(snap, "engine_position", None)
                        or player.get("engine_position")
                    )
                )
                calc_stats = dict(updates.get("stats") or getattr(snap, "stats", None) or {})
                required = list(POSITION_STATS[calc_position])
                if calc_stats and all(stat in calc_stats for stat in required):
                    state = PlayerState(
                        stats={stat: int(round(float(calc_stats[stat]))) for stat in required},
                        stat_names=required,
                    )
                    updates["rating"] = float(calculate_rating(state, calc_position, stat_mode="fractional"))
            except Exception:
                pass
            try:
                avatar_url = getattr(snap, "avatar_url", None)
                if avatar_url:
                    updates["avatar_url"] = avatar_url
            except Exception:
                pass
            try:
                image_bytes = getattr(snap, "avatar_image_bytes", None)
                if image_bytes:
                    updates["avatar_image_bytes"] = image_bytes
                    updates["avatar_content_type"] = getattr(snap, "avatar_content_type", None)
            except Exception:
                pass
            if updates:
                updates["_profile_enriched"] = True
                updates["_profile_enriched_at"] = time.time()
                return updates
        except Exception:
            pass

        # Fallback: keep the old XP-only path, so this change cannot make
        # group import less useful if VMAN blocks a profile page.
        try:
            xp = self._fetch_group_player_xp_only(url)
            if xp is not None:
                updates["xp"] = float(xp)
        except Exception:
            pass
        if updates:
            updates["_profile_enriched"] = False
        return updates

    def _enrich_group_player_xp_from_profile(self, player):
        """Fill missing training XP for a group-import player."""
        try:
            if player.get("xp") is not None:
                return True
            updates = self._fetch_group_player_profile_enrichment(player)
            if updates.get("xp") is not None:
                player["xp"] = updates.get("xp")
                enrichment_keys = ["age", "rating", "stats"]
                if str(player.get("source", "") or "") != "club_players_data":
                    enrichment_keys.extend(["position_code", "engine_position"])
                for key in enrichment_keys:
                    if updates.get(key) is not None:
                        player[key] = updates[key]
                return True
            return False
        except Exception:
            return False

    def _group_player_key(self, player):
        m = re.search(r"/players/(\d+)", str((player or {}).get("url", "")))
        return m.group(1) if m else str((player or {}).get("url", ""))

    def _group_player_has_xp(self, player):
        try:
            value = (player or {}).get("xp")
            if value is None or value == "" or value == "-":
                return False
            return float(str(value).replace(",", ".")) >= 0
        except Exception:
            return False

    def _fetch_group_player_avatar_only(self, player):
        """Fetch only the avatar bytes for one group player and keep a debug trail."""
        record = {
            "player": (player or {}).get("name", ""),
            "key": self._group_player_key(player),
            "url": str((player or {}).get("url", "") or ""),
            "attempts": [],
            "chosen_avatar_url": None,
            "success": False,
            "error": "",
        }
        try:
            import requests
            from .player_fetcher import (
                USER_AGENT,
                REQUEST_TIMEOUT,
                player_url_candidates,
                parse_player_avatar_url_from_html,
                fetch_avatar_image_bytes,
            )
            try:
                from .player_fetcher import _avatar_candidate_urls_from_html
            except Exception:
                _avatar_candidate_urls_from_html = None

            url = str((player or {}).get("url", "") or "").strip()
            key = self._group_player_key(player)
            candidates = []
            try:
                candidates.extend(player_url_candidates(url or key))
            except Exception:
                if url:
                    candidates.append(url)
            if key and str(key).isdigit():
                candidates.extend([
                    f"https://www.virtualmanager.com/da/players/{key}",
                    f"https://www.virtualmanager.com/en/players/{key}",
                    f"https://www.virtualmanager.com/players/{key}",
                ])
            seen = set()
            session = requests.Session()
            session.headers.update({
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "da-DK,da;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": "https://www.virtualmanager.com/",
            })
            for ref in candidates:
                if not ref or ref in seen:
                    continue
                seen.add(ref)
                attempt = {"ref": ref, "status": None, "final_url": "", "avatar_url": None, "candidate_count": 0, "error": ""}
                try:
                    resp = session.get(ref, timeout=REQUEST_TIMEOUT, allow_redirects=True)
                    attempt["status"] = getattr(resp, "status_code", None)
                    attempt["final_url"] = getattr(resp, "url", ref) or ref
                    resp.raise_for_status()
                    html = resp.text or ""
                    avatar_url = parse_player_avatar_url_from_html(html, attempt["final_url"])
                    if _avatar_candidate_urls_from_html is not None:
                        try:
                            attempt["candidate_count"] = len(_avatar_candidate_urls_from_html(html, attempt["final_url"]))
                        except Exception:
                            pass
                    attempt["avatar_url"] = avatar_url
                    if not avatar_url:
                        record["attempts"].append(attempt)
                        continue
                    image_bytes, content_type = fetch_avatar_image_bytes(avatar_url, session, referer=attempt["final_url"])
                    if image_bytes:
                        record["chosen_avatar_url"] = avatar_url
                        record["success"] = True
                        record["content_type"] = content_type
                        record["bytes"] = len(image_bytes or b"")
                        record["attempts"].append(attempt)
                        return image_bytes, avatar_url, content_type
                except Exception as error:
                    attempt["error"] = repr(error)
                    record["attempts"].append(attempt)
                    continue
        except Exception as error:
            record["error"] = repr(error)
        finally:
            try:
                self._group_avatar_debug_records.append(record)
            except Exception:
                pass
        return None, None, None

    def _group_target_position_for_players(self, players):
        """Choose the main-window position used for the group average.

        Players no longer have to share exact position type. For outfield mixes,
        the current main-window position is preferred when possible; otherwise the
        first outfield position is used. Pure goalkeeper groups stay as keepers.
        """
        players = list(players or [])
        if not players:
            return internal_position(self.position_var.get())
        try:
            current = internal_position(self.position_var.get())
        except Exception:
            current = "Forsvar"
        positions = []
        for player in players:
            try:
                positions.append(internal_position(player.get("engine_position", current)))
            except Exception:
                positions.append(current)
        mark_positions = [pos for pos in positions if pos != "Keepere"]
        if mark_positions:
            if current != "Keepere":
                return current
            return mark_positions[0]
        return "Keepere"

    def _start_group_xp_background_fetch(self, players):
        def needs_profile_enrichment(player):
            try:
                if not str(player.get("url", "") or "").strip():
                    return False
                if player.get("xp") is None:
                    return True
                # Club-list rows are intentionally enriched from the profile,
                # because window.playersData can contain rounded age/VT values.
                if str(player.get("source", "") or "") == "club_players_data" and not player.get("_profile_enriched"):
                    return True
                return False
            except Exception:
                return False

        players = [p for p in (players or []) if needs_profile_enrichment(p)]
        if not players:
            return
        self._group_xp_fetch_generation = int(getattr(self, "_group_xp_fetch_generation", 0) or 0) + 1
        generation = self._group_xp_fetch_generation
        q = queue.Queue()
        self._group_xp_fetch_queue = q
        self._group_xp_fetch_total = len(players)
        self._group_xp_fetch_done = 0
        self._group_xp_fetch_missing = 0

        def worker():
            # Fetch XP plus precise profile metadata concurrently. A full club is
            # usually around 25-30 players; keeping it in the background makes
            # the import window usable immediately.
            try:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                tasks = []
                max_workers = max(1, min(8, len(players)))
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    for player in list(players):
                        key = self._group_player_key(player)
                        tasks.append((key, executor.submit(self._fetch_group_player_profile_enrichment, copy.deepcopy(player))))
                    future_to_key = {future: key for key, future in tasks}
                    for future in as_completed(future_to_key):
                        key = future_to_key[future]
                        try:
                            updates = future.result() or {}
                        except Exception:
                            updates = {}
                        q.put((generation, key, updates))
            except Exception:
                # Conservative fallback if concurrent.futures is unavailable.
                for player in list(players):
                    key = self._group_player_key(player)
                    updates = self._fetch_group_player_profile_enrichment(copy.deepcopy(player)) or {}
                    q.put((generation, key, updates))
            q.put((generation, "__done__", None))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        if hasattr(self, "group_import_status_var"):
            self.group_import_status_var.set(
                self._group_text(
                    f"Henter præcis alder, VT og XP i baggrunden for {len(players)} spiller(e)…",
                    f"Fetching precise age, rating and XP in the background for {len(players)} player(s)…",
                )
            )
        self.after(80, self._drain_group_xp_fetch_queue)

    def _drain_group_xp_fetch_queue(self):
        q = getattr(self, "_group_xp_fetch_queue", None)
        if q is None:
            return
        generation = int(getattr(self, "_group_xp_fetch_generation", 0) or 0)
        changed_iids = []
        changed_keys = set()
        done_signal = False
        processed = 0

        def normalize_updates(payload):
            if isinstance(payload, dict):
                return dict(payload)
            if payload is None:
                return {}
            return {"xp": payload}

        def apply_updates_to_player(player, updates):
            changed = False
            if not player or not updates:
                return False
            for field in (
                "name", "age", "rating", "xp", "stats", "position_code",
                "engine_position", "avatar_url", "avatar_image_bytes", "avatar_content_type",
                "_profile_enriched", "_profile_enriched_at",
            ):
                if field not in updates:
                    continue
                # Never let the asynchronous profile/XP enrichment rewrite a
                # position that was already resolved correctly from club-list
                # playersData. This is the exact transition that previously
                # turned attackers into defenders when XP and VT arrived.
                if (
                    str(player.get("source", "") or "") == "club_players_data"
                    and field in {"position_code", "engine_position"}
                ):
                    continue
                value = updates.get(field)
                if value is None or value == "":
                    continue
                try:
                    if field in {"age", "rating", "xp"}:
                        value = float(str(value).replace(",", "."))
                    elif field == "engine_position":
                        value = internal_position(value)
                except Exception:
                    pass
                player[field] = copy.deepcopy(value)
                changed = True
            return changed

        def apply_updates_by_key(key, updates):
            changed_any = False
            for idx, player in enumerate(getattr(self, "group_import_players", []) or []):
                if self._group_player_key(player) == key:
                    if apply_updates_to_player(player, updates):
                        changed_iids.append(str(idx))
                        changed_any = True
            active = getattr(self, "active_group", None)
            if active:
                for player in active.get("players", []) or []:
                    if self._group_player_key(player) == key:
                        changed_any = apply_updates_to_player(player, updates) or changed_any
            for group in getattr(self, "group_import_groups", []) or []:
                for player in group.get("players", []) or []:
                    if self._group_player_key(player) == key:
                        changed_any = apply_updates_to_player(player, updates) or changed_any
            for entry in getattr(self, "group_report_history", []) or []:
                entry_changed = False
                for player in entry.get("players", []) or []:
                    if self._group_player_key(player) == key:
                        entry_changed = apply_updates_to_player(player, updates) or entry_changed
                if entry_changed:
                    # Existing rows may have been calculated from rounded
                    # age/VT. Clear them so the next refresh/recompute derives
                    # values from the enriched player data.
                    entry["simulated"] = False
                    entry["rows"] = []
                    changed_any = True
            return changed_any

        try:
            while processed < 40:
                item_generation, key, payload = q.get_nowait()
                processed += 1
                if item_generation != generation:
                    continue
                if key == "__done__":
                    done_signal = True
                    continue
                self._group_xp_fetch_done = int(getattr(self, "_group_xp_fetch_done", 0) or 0) + 1
                updates = normalize_updates(payload)
                if not updates:
                    self._group_xp_fetch_missing = int(getattr(self, "_group_xp_fetch_missing", 0) or 0) + 1
                    continue
                if apply_updates_by_key(key, updates):
                    changed_keys.add(key)
                if updates.get("xp") is None:
                    self._group_xp_fetch_missing = int(getattr(self, "_group_xp_fetch_missing", 0) or 0) + 1
        except queue.Empty:
            pass

        if changed_iids:
            self._group_update_tree_rows(sorted(set(changed_iids), key=lambda value: int(value)), sync_selection=True)
            sort_state = getattr(self, "_group_import_sort_state", None)
            if sort_state:
                self._sort_group_import_tree(sort_state[0], sort_state[1])

        if changed_keys:
            try:
                self._refresh_presets_menu()
            except Exception:
                pass
            self._refresh_group_report_window()

        if hasattr(self, "group_import_status_var"):
            done = int(getattr(self, "_group_xp_fetch_done", 0) or 0)
            total = int(getattr(self, "_group_xp_fetch_total", 0) or 0)
            if total and not done_signal:
                self.group_import_status_var.set(
                    self._group_text(
                        f"Henter præcis alder, VT og XP… {min(done, total)}/{total}",
                        f"Fetching precise age, rating and XP… {min(done, total)}/{total}",
                    )
                )

        if done_signal:
            missing = int(getattr(self, "_group_xp_fetch_missing", 0) or 0)
            total = int(getattr(self, "_group_xp_fetch_total", 0) or 0)
            if hasattr(self, "group_import_status_var"):
                if missing:
                    self.group_import_status_var.set(
                        self._group_text(
                            f"Præcise data hentet for {max(0, total - missing)} af {total} spiller(e).",
                            f"Precise data fetched for {max(0, total - missing)} of {total} player(s).",
                        )
                    )
                else:
                    self.group_import_status_var.set(self._group_text("Præcis alder, VT og XP hentet.", "Precise age, rating and XP fetched."))
            program_for_report, _source = self._group_report_program()
            if getattr(self, "active_group", None) and program_for_report:
                self._schedule_group_report_recompute(delay=120)
            else:
                self._refresh_group_report_window()
            return

        self.after(80, self._drain_group_xp_fetch_queue)

    def _club_players_url_candidates(self, raw_url):
        raw_url = self._normalize_group_input_url(raw_url)
        m = re.search(r"/clubs/(\d+)(?:/players)?", raw_url)
        if not m:
            return []
        club_id = m.group(1)
        candidates = []
        if re.match(r"^https?://", raw_url, flags=re.IGNORECASE):
            parts = urlsplit(raw_url)
            path = parts.path.rstrip("/")
            if not path.endswith("/players"):
                path = path + "/players"
            candidates.append(urlunsplit((parts.scheme or "https", parts.netloc or "www.virtualmanager.com", path, "", "")))
        candidates.extend([
            f"https://www.virtualmanager.com/da/clubs/{club_id}/players",
            f"https://www.virtualmanager.com/clubs/{club_id}/players",
            f"https://www.virtualmanager.com/en/clubs/{club_id}/players",
        ])
        out = []
        for url in candidates:
            if url not in out:
                out.append(url)
        return out

    def _fetch_player_links_from_club(self, club_url):
        """Fetch all player-profile links from a Virtual Manager club players page.

        3.43: More defensive extraction + debug files. If VMAN changes the
        club-page HTML or blocks a request, the debug report makes the failure
        inspectable instead of just returning "no links".
        """
        try:
            import requests
        except Exception as error:
            raise RuntimeError("requests mangler. Installer afhængighederne først." if self._language_code() != "en" else "requests is missing. Install the dependencies first.") from error

        self._last_group_import_club_players = []
        normalized = self._normalize_group_input_url(club_url)
        m_club = re.search(r"/clubs/(\d+)", normalized)
        club_id = m_club.group(1) if m_club else ""

        session = requests.Session()
        try:
            from .player_fetcher import USER_AGENT, REQUEST_TIMEOUT
        except Exception:
            USER_AGENT, REQUEST_TIMEOUT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/127 Safari/537.36", 20
        session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "da-DK,da;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": "https://www.virtualmanager.com/",
        })

        attempts = []
        html_by_url = {}
        html_pages = []
        last_error = None

        # Hit front page first to pick up any public cookies/locale redirects.
        try:
            session.get("https://www.virtualmanager.com/da", timeout=REQUEST_TIMEOUT, allow_redirects=True)
        except Exception:
            pass

        candidates = self._club_players_url_candidates(club_url)
        # Add simple pagination candidates. VMAN usually shows all players, but
        # this also catches table pagination if present without hurting normal use.
        more_candidates = []
        for url in candidates:
            more_candidates.append(url)
            for param in ("page=1", "page=2", "page=3"):
                sep = "&" if "?" in url else "?"
                more_candidates.append(url + sep + param)
        candidates = []
        for url in more_candidates:
            if url not in candidates:
                candidates.append(url)

        for url in candidates:
            try:
                resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
                html = resp.text or ""
                attempts.append({
                    "url": url,
                    "final_url": getattr(resp, "url", url),
                    "status": getattr(resp, "status_code", None),
                    "ok": bool(getattr(resp, "ok", False)),
                    "bytes": len(html),
                    "error": "",
                })
                html_by_url[url] = html
                if resp.status_code == 403:
                    last_error = "403 Forbidden"
                    continue
                resp.raise_for_status()
                if html:
                    html_pages.append((resp.url or url, html))
            except Exception as error:
                last_error = error
                attempts.append({"url": url, "final_url": "", "status": "", "ok": False, "bytes": 0, "error": repr(error)})

        if not html_pages:
            self._last_group_import_debug_path = ""
            raise RuntimeError(
                (f"Kunne ikke hente klubspillere: {last_error}.")
                if self._language_code() != "en"
                else (f"Could not fetch club players: {last_error}.")
            )

        direct_players = []
        direct_seen_ids = set()
        for base_url, html in html_pages:
            for player in self._extract_group_players_from_players_data(html, base_url):
                m = re.search(r"/players/(\d+)", str(player.get("url", "")))
                pid = m.group(1) if m else str(player.get("url", ""))
                if pid and pid not in direct_seen_ids:
                    direct_seen_ids.add(pid)
                    direct_players.append(player)
        self._last_group_import_club_players = direct_players

        refs = []
        seen_ids = set()

        def add_ref(raw_ref, base_url=None):
            raw_ref = str(raw_ref or "").strip()
            if not raw_ref:
                return
            # Club pages can contain escaped JSON URL fragments.
            raw_ref = raw_ref.replace(r"\/", "/").replace("&amp;", "&")
            m = re.search(r"/players/(\d+)", raw_ref)
            if not m:
                # Some table/script fragments expose only a player id.
                m = re.search(r"(?:player[_-]?id|playerId|data-player-id)[\"'\s:=]+(\d+)", raw_ref, re.IGNORECASE)
                if not m:
                    return
                raw_ref = f"/da/players/{m.group(1)}"
            player_id = m.group(1)
            if player_id in seen_ids:
                return
            seen_ids.add(player_id)
            refs.append(urljoin(base_url or "https://www.virtualmanager.com/", raw_ref))

        for base_url, html in html_pages:
            try:
                import html as html_lib
                html_for_scan = html_lib.unescape(html)
            except Exception:
                html_for_scan = html
            html_for_scan = html_for_scan.replace(r"\/", "/")

            # 0) VMAN club pages render the list as window.playersData JSON.
            for player_id in self._extract_club_player_ids_from_players_data(html_for_scan):
                add_ref(f"/da/players/{player_id}", base_url)

            # 1) Direct raw HTML/JSON scan, including escaped href/data/onclick fragments.
            for m in re.finditer(r"(?:https?://[^\"'<>\s]+)?/(?:[a-z]{2}/)?players/\d+[^\"'<>\s]*", html_for_scan, re.IGNORECASE):
                add_ref(m.group(0).rstrip(",.;)"), base_url)

            # 2) Known player-id attributes in tables/scripts.
            for pat in [
                r"data-player-id=[\"']?(\d+)",
                r"player[_-]?id[\"'\s:=]+(\d+)",
                r"playerId[\"'\s:=]+(\d+)",
            ]:
                for m in re.finditer(pat, html_for_scan, re.IGNORECASE):
                    add_ref(f"/da/players/{m.group(1)}", base_url)

            # 3) Anchor/data attributes via BeautifulSoup when available.
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html_for_scan, "html.parser")
                for tag in soup.find_all(True):
                    for attr in ("href", "data-href", "data-url", "data-link", "data-player-id", "onclick"):
                        value = tag.get(attr)
                        if value:
                            value = str(value).replace(r"\/", "/")
                            for m in re.finditer(r"(?:https?://[^\"'<>\s]+)?/(?:[a-z]{2}/)?players/\d+[^\"'<>\s]*", value, re.IGNORECASE):
                                add_ref(m.group(0).rstrip(",.;)"), base_url)
                            if re.fullmatch(r"\d+", str(value).strip()):
                                add_ref(f"/da/players/{value}", base_url)
            except Exception:
                pass

        self._last_group_import_debug_path = ""

        if not refs:
            raise RuntimeError(
                "Klubsiden blev hentet, men der blev ikke fundet spillerlinks."
                if self._language_code() != "en"
                else "The club page was fetched, but no player links were found."
            )
        return refs

    def _group_extract_references(self, text):
        raw = str(text or "")
        tokens = []
        for m in re.finditer(r"https?://[^\s<>()]+|(?:www\.)?virtualmanager\.com/[^\s<>()]+|\b\d{6,}\b", raw, flags=re.IGNORECASE):
            token = m.group(0).strip().rstrip(",.;)")
            if token:
                tokens.append(token)
        if not tokens:
            return []

        refs = []
        seen = set()
        for token in tokens:
            normalized = self._normalize_group_input_url(token)
            if re.search(r"/clubs/\d+", normalized):
                for ref in self._fetch_player_links_from_club(normalized):
                    key = re.search(r"/players/(\d+)", ref)
                    key = key.group(1) if key else ref
                    if key not in seen:
                        seen.add(key)
                        refs.append(ref)
            else:
                key_match = re.search(r"/players/(\d+)", normalized)
                key = key_match.group(1) if key_match else normalized
                if key not in seen:
                    seen.add(key)
                    refs.append(normalized)
        return refs

    def _open_group_import_from_player_button(self):
        """Åbn Gruppeimport fra spillersektionen.

        Hvis spillerlinkfeltet indeholder et hold-/klublink, bruges linket og
        spillerne hentes automatisk. Ellers åbnes Gruppeimport blot tomt.
        """
        try:
            reference = self._current_player_link_reference() if hasattr(self, "_current_player_link_reference") else ""
            normalized = self._normalize_group_input_url(reference) if reference else ""
            if normalized and re.search(r"/clubs/\d+", normalized, flags=re.IGNORECASE):
                self._open_group_import_from_main_link(normalized)
                return "break"
        except Exception:
            pass
        self._show_group_import_window()
        return "break"

    def _open_group_import_window(self, event=None):
        self._show_group_import_window()
        return "break"

    def _show_group_import_window(self):
        if self.group_import_window is not None and self.group_import_window.winfo_exists():
            self.group_import_window.lift()
            return
        win = tk.Toplevel(self)
        self.group_import_window = win
        win.title(self._group_text("Gruppeimport", "Group import"))
        win.geometry("920x640")
        win.transient(self)
        win.protocol("WM_DELETE_WINDOW", self._close_group_import_window)
        win.bind("<Destroy>", self._group_import_window_destroyed, add="+")

        top = ttk.Frame(win, padding=10)
        top.pack(fill="both", expand=True)
        ttk.Label(top, text=self._group_text("Indsæt spillerlinks eller klublink", "Paste player links or club link")).pack(anchor="w")
        if not hasattr(self, "group_import_input_var"):
            self.group_import_input_var = tk.StringVar(value="")
        self.group_import_entry = ttk.Entry(top, textvariable=self.group_import_input_var)
        self.group_import_entry.pack(fill="x", pady=(4, 8))
        self.group_import_entry.bind("<Button-1>", lambda event: (event.widget.focus_set(), None)[-1])
        self.group_import_entry.bind("<Return>", lambda event: self._group_import_fetch())
        self.group_import_entry.bind("<Command-a>", lambda event: (self.group_import_entry.selection_range(0, "end"), "break")[-1])
        self.group_import_entry.bind("<Command-A>", lambda event: (self.group_import_entry.selection_range(0, "end"), "break")[-1])
        self.group_import_entry.bind("<Control-a>", lambda event: (self.group_import_entry.selection_range(0, "end"), "break")[-1])
        self.group_import_entry.bind("<Control-A>", lambda event: (self.group_import_entry.selection_range(0, "end"), "break")[-1])
        win.after(120, lambda: (self.group_import_entry.focus_set(), self.group_import_entry.icursor("end")))

        controls = ttk.Frame(top)
        controls.pack(fill="x", pady=(0, 8))
        self.group_import_fetch_button = ttk.Button(controls, text=self._group_text("Hent liste", "Fetch list"), command=self._group_import_fetch)
        self.group_import_fetch_button.pack(side="left")
        ttk.Button(controls, text=self._group_text("Fjern valgte", "Remove selected"), command=self._group_remove_selected_from_import).pack(side="left", padx=(6, 0))
        ttk.Button(controls, text=self._group_text("Tilføj til", "Add to"), command=self._group_add_selected_to_main).pack(side="right")

        group_controls = ttk.Frame(top)
        group_controls.pack(fill="x", pady=(0, 8))
        ttk.Label(group_controls, text=self._group_text("Aktiv gruppe:", "Active group:")).pack(side="left")
        if not hasattr(self, "group_active_group_var"):
            self.group_active_group_var = tk.StringVar(value="")
        self.group_active_group_box = self._create_fixed_dropdown(group_controls, self.group_active_group_var, values=[], width_px=300)
        self.group_active_group_box.pack(side="left", padx=(6, 6))
        self.group_active_group_box.bind("<<ComboboxSelected>>", lambda event: self._activate_group_from_import_selector())
        ttk.Button(group_controls, text=self._group_text("Anvend i kontrolcenter", "Apply in Control Center"), command=self._activate_group_from_import_selector).pack(side="left")
        ttk.Button(group_controls, text=self._group_text("Fjern gruppe", "Remove group"), command=self._remove_group_import_group).pack(side="left", padx=(6, 0))
        columns = ("use", "name", "age", "position", "rating", "xp", "group")
        self.group_import_tree = ttk.Treeview(top, columns=columns, show="headings", selectmode="extended", height=15)
        headings = {
            "use": "☐",
            "name": self._group_text("Navn", "Name"),
            "age": self._group_text("Alder", "Age"),
            "position": self._group_text("Position", "Position"),
            "rating": self._group_text("VT", "Rating"),
            "xp": "XP",
            "group": self._group_text("Gruppe", "Group"),
        }
        for col in columns:
            if col == "use":
                self.group_import_tree.heading(col, text=headings[col], command=self._group_toggle_all_from_header)
            else:
                self.group_import_tree.heading(col, text=headings[col], command=lambda c=col: self._sort_group_import_tree(c))
        self.group_import_tree.column("use", width=48, anchor="center", stretch=False)
        self.group_import_tree.column("name", width=280, anchor="w", stretch=True)
        self.group_import_tree.column("age", width=80, anchor="center", stretch=False)
        self.group_import_tree.column("position", width=110, anchor="center", stretch=False)
        self.group_import_tree.column("rating", width=80, anchor="center", stretch=False)
        self.group_import_tree.column("xp", width=80, anchor="center", stretch=False)
        self.group_import_tree.column("group", width=150, anchor="w", stretch=False)
        self.group_import_tree.pack(fill="both", expand=True)
        self._group_import_selection_anchor = None
        self._group_import_selection_mode = "rows"
        self._group_import_refreshing = False
        self.group_import_tree.bind("<Button-1>", self._group_tree_click)
        self.group_import_tree.bind("<Command-Button-1>", self._group_tree_command_click)
        self.group_import_tree.bind("<Control-Button-1>", self._group_tree_command_click)
        self.group_import_tree.bind("<Shift-Button-1>", self._group_tree_shift_click)
        self.group_import_tree.bind("<<TreeviewSelect>>", self._group_tree_selection_changed)
        for sequence in ("<Command-a>", "<Command-A>", "<Control-a>", "<Control-A>"):
            self.group_import_tree.bind(sequence, self._group_tree_select_all_event)
            win.bind(sequence, self._group_tree_select_all_event)
        self.group_import_status_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.group_import_status_var).pack(anchor="w", pady=(6, 0))
        self._group_refresh_tree()
        self._refresh_group_selector((getattr(self, "active_group", None) or {}).get("id"))

    def _cancel_pending_group_import_auto_open(self):
        after_id = getattr(self, "_player_link_group_import_after_id", None)
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except Exception:
                pass
        self._player_link_group_import_after_id = None

    def _close_group_import_window(self):
        try:
            current = self._current_player_link_reference() if hasattr(self, "_current_player_link_reference") else ""
            current = self._normalize_group_input_url(current) if current else ""
            if current and re.search(r"/clubs/\d+", current, flags=re.IGNORECASE):
                self._group_import_auto_open_blocked_link = current
        except Exception:
            pass
        self._cancel_pending_group_import_auto_open()
        fetch_after_id = getattr(self, "_group_import_fetch_after_id", None)
        if fetch_after_id is not None:
            try:
                self.after_cancel(fetch_after_id)
            except Exception:
                pass
        self._group_import_fetch_after_id = None
        self._group_import_fetch_generation = int(getattr(self, "_group_import_fetch_generation", 0) or 0) + 1
        try:
            win = getattr(self, "group_import_window", None)
            if win is not None and win.winfo_exists():
                win.destroy()
        except Exception:
            pass
        self.group_import_window = None

    def _group_import_window_destroyed(self, event=None):
        try:
            if event is not None and event.widget is not getattr(self, "group_import_window", None):
                return
        except Exception:
            pass
        self.group_import_window = None
        self.group_import_fetch_button = None

    def _set_group_import_fetching(self, fetching):
        state = "disabled" if fetching else "normal"
        for attr in ("group_import_fetch_button", "group_import_entry"):
            widget = getattr(self, attr, None)
            try:
                if widget is not None and widget.winfo_exists():
                    widget.configure(state=state)
            except Exception:
                pass

    def _group_tree_iid_at_event(self, event):
        tree = getattr(self, "group_import_tree", None)
        if tree is None:
            return None
        try:
            iid = tree.identify_row(event.y)
            return iid if iid != "" else None
        except Exception:
            return None

    def _group_tree_valid_iids(self, iids):
        out = []
        for iid in iids or []:
            try:
                idx = int(iid)
                if 0 <= idx < len(self.group_import_players):
                    out.append(str(idx))
            except Exception:
                pass
        return out

    def _group_tree_values_for_player(self, idx, player):
        pos = self._display_position(player.get("engine_position", "Forsvar"))
        rating = player.get("rating")
        if rating is None:
            rating = self._group_player_start_rating(player, player.get("engine_position"))
            player["rating"] = rating
        has_xp = self._group_player_has_xp(player)
        assigned_group = str(player.get("assigned_group", "") or "").strip()
        if assigned_group or not has_xp:
            player["selected"] = False
        is_selected = bool(player.get("selected", False)) and has_xp and not assigned_group
        marker = "↶" if assigned_group else (("☑" if is_selected else "☐") if has_xp else "")
        return (
            marker,
            player.get("name", "Ukendt"),
            self._group_import_format_num(player.get("age"), 2),
            pos,
            self._group_import_format_num(rating, 2),
            self._group_format_num(player.get("xp"), 0),
            player.get("assigned_group", "") or "",
        )

    def _group_update_tree_rows(self, iids=None, sync_selection=True):
        tree = getattr(self, "group_import_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        try:
            if iids is None:
                iids = [str(i) for i in range(len(self.group_import_players))]
            iids = self._group_tree_valid_iids(iids)
            self._group_import_refreshing = True
            for iid in iids:
                idx = int(iid)
                if tree.exists(iid):
                    tree.item(iid, values=self._group_tree_values_for_player(idx, self.group_import_players[idx]))
            if sync_selection:
                selected_iids = [str(i) for i, p in enumerate(self.group_import_players) if bool(p.get("selected", False)) and self._group_player_has_xp(p) and tree.exists(str(i))]
                if selected_iids:
                    tree.selection_set(selected_iids)
                else:
                    tree.selection_remove(tree.selection())
        except Exception:
            pass
        finally:
            self._group_import_refreshing = False
            self._group_update_header_checkbox()

    def _group_update_header_checkbox(self):
        tree = getattr(self, "group_import_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        selectable = [p for p in (getattr(self, "group_import_players", []) or []) if self._group_player_has_xp(p) and not str(p.get("assigned_group", "") or "").strip()]
        total = len(selectable)
        selected = sum(1 for p in selectable if bool(p.get("selected", False)))
        text = "☑" if total and selected == total else "☐"
        try:
            tree.heading("use", text=text, command=self._group_toggle_all_from_header)
        except Exception:
            pass

    def _group_toggle_all_from_header(self):
        selectable = [p for p in (getattr(self, "group_import_players", []) or []) if self._group_player_has_xp(p) and not str(p.get("assigned_group", "") or "").strip()]
        total = len(selectable)
        selected = sum(1 for p in selectable if bool(p.get("selected", False)))
        self._group_set_selected(not (total and selected == total))

    def _group_sync_selected_flags_from_tree(self):
        tree = getattr(self, "group_import_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        try:
            selected = set(self._group_tree_valid_iids(tree.selection()))
            changed = []
            for idx, player in enumerate(self.group_import_players):
                new_value = str(idx) in selected and self._group_player_has_xp(player) and not str(player.get("assigned_group", "") or "").strip()
                if bool(player.get("selected", False)) != new_value:
                    player["selected"] = new_value
                    changed.append(str(idx))
            if changed:
                self._group_update_tree_rows(changed, sync_selection=False)
            self._group_update_header_checkbox()
        except Exception:
            pass

    def _sort_group_import_tree(self, col, reverse=None):
        tree = getattr(self, "group_import_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        if col == "use":
            self._group_toggle_all_from_header()
            return
        if reverse is None:
            current = getattr(self, "_group_import_sort_state", None)
            reverse = bool(current and current[0] == col and not current[1])

        def raw_value(player):
            try:
                if col == "name":
                    return player.get("name", "")
                if col == "age":
                    return player.get("age")
                if col == "position":
                    return self._display_position(player.get("engine_position", "Forsvar"))
                if col == "rating":
                    value = player.get("rating")
                    if value is None:
                        value = self._group_player_start_rating(player, player.get("engine_position"))
                    return value
                if col == "xp":
                    return player.get("xp")
                if col == "group":
                    return player.get("assigned_group", "") or ""
            except Exception:
                pass
            return ""

        def sort_key(player):
            value = raw_value(player)
            # Empty values shown as '-' should sort as smaller than real numbers.
            # With descending sort they naturally move to the bottom.
            if value in {None, "", "-"}:
                return (0, "")
            try:
                return (1, float(str(value).replace(",", ".").replace("+", "")))
            except Exception:
                return (1, str(value).lower())

        try:
            self.group_import_players.sort(key=sort_key, reverse=bool(reverse))
        except Exception:
            return
        self._group_import_sort_state = (col, bool(reverse))
        self._group_refresh_tree(apply_sort=False)
        try:
            for c in ("name", "age", "position", "rating", "xp", "group"):
                self.group_import_tree.heading(c, command=lambda cc=c: self._sort_group_import_tree(cc))
            tree.heading(col, command=lambda c=col, r=not bool(reverse): self._sort_group_import_tree(c, r))
        except Exception:
            pass

    def _group_apply_selected_state_to_tree(self):
        self._group_update_tree_rows(sync_selection=True)

    def _group_set_selected_for_iids(self, iids, selected=None, exclusive=False, refresh=True):
        iids = self._group_tree_valid_iids(iids)
        changed = set(iids)
        if exclusive:
            for idx, player in enumerate(self.group_import_players):
                if player.get("selected", False):
                    changed.add(str(idx))
                player["selected"] = False
        if not iids:
            if refresh:
                self._group_update_tree_rows(changed or None, sync_selection=True)
            return
        if selected is None:
            first = self.group_import_players[int(iids[0])].get("selected", False)
            selected = not bool(first)
        for iid in iids:
            player = self.group_import_players[int(iid)]
            if not self._group_player_has_xp(player) or str(player.get("assigned_group", "") or "").strip():
                player["selected"] = False
                changed.add(iid)
                continue
            player["selected"] = bool(selected)
            changed.add(iid)
        if refresh:
            self._group_update_tree_rows(changed, sync_selection=True)

    def _group_tree_selection_changed(self, event=None):
        if getattr(self, "_group_import_refreshing", False):
            return
        # Let Tk finish native Command/Shift selection before mirroring it into
        # the Medtag column. This avoids the freeze/feedback loop seen on macOS.
        try:
            self.after_idle(self._group_sync_selected_flags_from_tree)
        except Exception:
            self._group_sync_selected_flags_from_tree()

    def _group_tree_select_all_event(self, event=None):
        self._group_set_selected(True)
        return "break"

    def _group_toggle_tree_selection(self):
        tree = getattr(self, "group_import_tree", None)
        if tree is None:
            return "break"
        iids = self._group_tree_valid_iids(tree.selection())
        if iids:
            self._group_set_selected_for_iids(iids)
        return "break"

    def _group_tree_click(self, event=None):
        tree = getattr(self, "group_import_tree", None)
        if tree is None:
            return "break"
        try:
            if tree.identify_region(event.x, event.y) == "heading":
                col_id = tree.identify_column(event.x)
                columns = list(tree.cget("columns") or [])
                idx = int(str(col_id).replace("#", "")) - 1
                if 0 <= idx < len(columns):
                    col = columns[idx]
                    if col == "use":
                        self._group_toggle_all_from_header()
                    else:
                        self._sort_group_import_tree(col)
                    return "break"
        except Exception:
            pass
        item = self._group_tree_iid_at_event(event)
        if not item:
            return None
        # 3.52: A normal mouse click anywhere across the row behaves like
        # clicking the checkbox: it toggles that player without clearing the
        # other selected rows. This makes multi-selection possible with the
        # mouse alone, without holding Command/Ctrl.
        try:
            player = self.group_import_players[int(item)]
            col_id = tree.identify_column(event.x)
            assigned_group = str(player.get("assigned_group", "") or "").strip()
            if assigned_group:
                if col_id == "#1":
                    self._group_unassign_player_from_group(player)
                else:
                    if hasattr(self, "group_import_status_var"):
                        self.group_import_status_var.set(self._group_text("Spilleren er allerede i en gruppe. Brug fortryd-ikonet for at fjerne den.", "The player is already in a group. Use the undo icon to remove it."))
                return "break"
            if not self._group_player_has_xp(player):
                player["selected"] = False
                tree.selection_remove(item)
                self._group_update_tree_rows([item], sync_selection=False)
                if hasattr(self, "group_import_status_var"):
                    self.group_import_status_var.set(self._group_text("Spilleren mangler XP og kan ikke tilføjes til en gruppe.", "This player has no XP and cannot be added to a group."))
                return "break"
            new_value = not bool(player.get("selected", False))
            player["selected"] = new_value
            if new_value:
                tree.selection_add(item)
            else:
                tree.selection_remove(item)
            tree.focus(item)
            self._group_import_selection_anchor = item
            self._group_update_tree_rows([item], sync_selection=False)
            self._group_update_header_checkbox()
        except Exception:
            pass
        return "break"

    def _group_tree_command_click(self, event=None):
        tree = getattr(self, "group_import_tree", None)
        item = self._group_tree_iid_at_event(event)
        if tree is None or not item:
            return "break"
        try:
            player = self.group_import_players[int(item)]
            assigned_group = str(player.get("assigned_group", "") or "").strip()
            if assigned_group:
                if tree.identify_column(event.x) == "#1":
                    self._group_unassign_player_from_group(player)
                return "break"
            if not self._group_player_has_xp(player):
                player["selected"] = False
                tree.selection_remove(item)
                self._group_update_tree_rows([item], sync_selection=False)
                if hasattr(self, "group_import_status_var"):
                    self.group_import_status_var.set(self._group_text("Spilleren mangler XP og kan ikke tilføjes til en gruppe.", "This player has no XP and cannot be added to a group."))
                return "break"
            if item in tree.selection():
                tree.selection_remove(item)
                player["selected"] = False
            else:
                tree.selection_add(item)
                player["selected"] = True
            tree.focus(item)
            self._group_import_selection_anchor = item
            self._group_update_tree_rows([item], sync_selection=False)
            self._group_update_header_checkbox()
        except Exception:
            pass
        return "break"

    def _group_tree_shift_click(self, event=None):
        tree = getattr(self, "group_import_tree", None)
        item = self._group_tree_iid_at_event(event)
        if tree is None or not item:
            return "break"
        anchor = getattr(self, "_group_import_selection_anchor", None) or item
        try:
            a = int(anchor)
            b = int(item)
            lo, hi = sorted((a, b))
            iids = [str(i) for i in range(lo, hi + 1) if 0 <= i < len(self.group_import_players)]
            for player in self.group_import_players:
                player["selected"] = False
            selectable_iids = []
            for iid in iids:
                player = self.group_import_players[int(iid)]
                if self._group_player_has_xp(player) and not str(player.get("assigned_group", "") or "").strip():
                    player["selected"] = True
                    selectable_iids.append(iid)
            tree.selection_set(selectable_iids)
            tree.focus(item)
            self._group_update_tree_rows(sync_selection=False)
            self._group_update_header_checkbox()
        except Exception:
            pass
        return "break"

    def _group_refresh_tree(self, apply_sort=True):
        tree = getattr(self, "group_import_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        try:
            self._group_import_refreshing = True
            tree.delete(*tree.get_children())
            selected_iids = []
            for idx, player in enumerate(self.group_import_players):
                if "selected" not in player:
                    player["selected"] = False
                if not self._group_player_has_xp(player) or str(player.get("assigned_group", "") or "").strip():
                    player["selected"] = False
                if bool(player.get("selected", False)):
                    selected_iids.append(str(idx))
                tree.insert("", "end", iid=str(idx), values=self._group_tree_values_for_player(idx, player))
            if selected_iids:
                tree.selection_set(selected_iids)
            else:
                tree.selection_remove(tree.selection())
        except Exception:
            pass
        finally:
            self._group_import_refreshing = False
            self._group_update_header_checkbox()
        sort_state = getattr(self, "_group_import_sort_state", None)
        if apply_sort and sort_state:
            self._sort_group_import_tree(sort_state[0], sort_state[1])

    def _group_set_selected(self, selected):
        self._group_import_selection_mode = "rows"
        for player in self.group_import_players:
            player["selected"] = bool(selected) if self._group_player_has_xp(player) and not str(player.get("assigned_group", "") or "").strip() else False
        self._group_update_tree_rows(sync_selection=True)

    def _group_import_existing_player_ids(self):
        existing_ids = set()
        for player in getattr(self, "group_import_players", []) or []:
            match = re.search(r"/players/(\d+)", str(player.get("url", "")))
            if match:
                existing_ids.add(match.group(1))
        return existing_ids

    def _group_import_fetch_worker(self, raw, existing_ids):
        refs = self._group_extract_references(raw)
        direct_players = list(getattr(self, "_last_group_import_club_players", []) or []) if re.search(r"/clubs/\d+", raw) else []
        if not refs and not direct_players:
            raise ValueError("Ingen spillerlinks fundet." if self._language_code() != "en" else "No player links found.")

        added_players = []
        errors = []
        seen_ids = set(existing_ids or set())

        def player_id_from_url(value):
            match = re.search(r"/players/(\d+)", str(value or ""))
            return match.group(1) if match else ""

        if direct_players:
            for player in direct_players:
                player = dict(player or {})
                player_id = player_id_from_url(player.get("url"))
                if player_id and player_id in seen_ids:
                    continue
                player["selected"] = False
                added_players.append(player)
                if player_id:
                    seen_ids.add(player_id)
        else:
            for ref in refs:
                player_id = player_id_from_url(ref)
                if player_id and player_id in seen_ids:
                    continue
                try:
                    snap = fetch_player_snapshot(ref)
                    player = self._snapshot_to_group_player(snap, source=ref)
                    added_players.append(player)
                    if player_id:
                        seen_ids.add(player_id)
                except Exception as error:
                    errors.append(f"{ref}: {error}")
        return {"players": added_players, "errors": errors}

    def _group_import_fetch(self):
        self._group_import_fetch_after_id = None
        entry_var = getattr(self, "group_import_input_var", None)
        if entry_var is not None:
            raw = str(entry_var.get() or "").strip()
        else:
            # Backwards-compatible fallback for older in-memory windows.
            text_widget = getattr(self, "group_import_text", None)
            if text_widget is None:
                return
            try:
                raw = text_widget.get("1.0", "end").strip()
            except TypeError:
                raw = text_widget.get().strip()
        if not raw:
            return

        self._group_import_fetch_generation = int(getattr(self, "_group_import_fetch_generation", 0) or 0) + 1
        generation = self._group_import_fetch_generation
        q = queue.Queue()
        self._group_import_fetch_queue = q
        existing_ids = self._group_import_existing_player_ids()
        if hasattr(self, "group_import_status_var"):
            self.group_import_status_var.set(self._group_text("Henter spillere…", "Fetching players…"))
        self._set_group_import_fetching(True)

        def worker():
            try:
                payload = self._group_import_fetch_worker(raw, existing_ids)
                q.put((generation, "done", payload))
            except Exception as error:
                q.put((generation, "error", str(error)))

        thread = threading.Thread(target=worker, daemon=True)
        self._group_import_fetch_thread = thread
        thread.start()
        self.after(80, self._drain_group_import_fetch_queue)

    def _drain_group_import_fetch_queue(self):
        q = getattr(self, "_group_import_fetch_queue", None)
        if q is None:
            return
        generation = int(getattr(self, "_group_import_fetch_generation", 0) or 0)
        try:
            item_generation, kind, payload = q.get_nowait()
        except queue.Empty:
            try:
                win = getattr(self, "group_import_window", None)
                if win is not None and win.winfo_exists():
                    self.after(80, self._drain_group_import_fetch_queue)
            except Exception:
                pass
            return

        if item_generation != generation:
            return

        try:
            win = getattr(self, "group_import_window", None)
            if win is None or not win.winfo_exists():
                return
        except Exception:
            return

        self._set_group_import_fetching(False)
        if kind == "error":
            if hasattr(self, "group_import_status_var"):
                self.group_import_status_var.set(str(payload))
            else:
                self._main_notice(str(payload))
            return

        players = list((payload or {}).get("players", []) or [])
        errors = list((payload or {}).get("errors", []) or [])
        existing_ids = self._group_import_existing_player_ids()
        added_players = []
        for player in players:
            match = re.search(r"/players/(\d+)", str(player.get("url", "")))
            player_id = match.group(1) if match else ""
            if player_id and player_id in existing_ids:
                continue
            self.group_import_players.append(player)
            added_players.append(player)
            if player_id:
                existing_ids.add(player_id)

        if added_players:
            self._group_refresh_tree()
            self._start_group_xp_background_fetch(added_players)
        entry_var = getattr(self, "group_import_input_var", None)
        if entry_var is not None:
            try:
                entry_var.set("")
            except Exception:
                pass
        if hasattr(self, "group_import_status_var"):
            base = self._group_text(f"Tilføjet {len(added_players)} spillere.", f"Added {len(added_players)} players.")
            if errors:
                base += self._group_text(f" {len(errors)} kunne ikke hentes.", f" {len(errors)} could not be fetched.")
            self.group_import_status_var.set(base)

    def _selected_group_iids(self):
        selected = set()
        tree = getattr(self, "group_import_tree", None)
        if tree is not None and tree.winfo_exists():
            try:
                selected.update(self._group_tree_valid_iids(tree.selection()))
            except Exception:
                pass
        for idx, player in enumerate(getattr(self, "group_import_players", []) or []):
            if bool(player.get("selected", False)):
                selected.add(str(idx))
        return sorted(selected, key=lambda value: int(value))

    def _group_remove_selected_from_import(self):
        iids = self._selected_group_iids()
        if not iids:
            messagebox.showinfo(
                self._group_text("Gruppeimport", "Group import"),
                self._group_text("Vælg mindst én spiller, der skal fjernes.", "Select at least one player to remove."),
                parent=getattr(self, "group_import_window", self),
            )
            return
        indexes = {int(iid) for iid in iids}
        removed = 0
        kept = []
        for idx, player in enumerate(getattr(self, "group_import_players", []) or []):
            if idx in indexes:
                removed += 1
                continue
            kept.append(player)
        self.group_import_players = kept
        self._group_import_selection_anchor = None
        self._group_refresh_tree(apply_sort=False)
        if hasattr(self, "group_import_status_var"):
            self.group_import_status_var.set(
                self._group_text(f"Fjernet {removed} spiller(e).", f"Removed {removed} player(s).")
            )

    def _selected_group_players(self):
        selected = []
        seen = set()
        def add_player(player):
            if not self._group_player_has_xp(player) or str(player.get("assigned_group", "") or "").strip():
                return
            key = self._group_player_key(player)
            if key not in seen:
                seen.add(key)
                selected.append(player)
        tree = getattr(self, "group_import_tree", None)
        if tree is not None and tree.winfo_exists():
            try:
                for iid in self._group_tree_valid_iids(tree.selection()):
                    add_player(self.group_import_players[int(iid)])
            except Exception:
                pass
        for p in self.group_import_players:
            if bool(p.get("selected", False)):
                add_player(p)
        return selected

    def _refresh_group_selector(self, selected_group_id=None):
        """Refresh the group selector in the Group import window."""
        groups = list(getattr(self, "group_import_groups", []) or [])
        labels = []
        used = {}
        label_to_id = {}
        for group in groups:
            name = str(group.get("name", "Gruppe") or "Gruppe")
            count = len(group.get("players", []) or [])
            base = f"{name} ({count})"
            label = base
            used[base] = used.get(base, 0) + 1
            if used[base] > 1:
                label = f"{base} #{used[base]}"
            labels.append(label)
            label_to_id[label] = group.get("id") or name
        self._group_selector_id_by_label = label_to_id
        box = getattr(self, "group_active_group_box", None)
        if box is not None:
            try:
                box.configure(values=labels)
            except Exception:
                pass
        var = getattr(self, "group_active_group_var", None)
        if var is not None:
            wanted = selected_group_id or (getattr(self, "active_group", None) or {}).get("id")
            chosen = ""
            if wanted:
                for label, gid in label_to_id.items():
                    if gid == wanted:
                        chosen = label
                        break
            if not chosen and labels:
                current = var.get()
                chosen = current if current in labels else labels[-1]
            var.set(chosen)

    def _selected_group_import_group_id(self):
        var = getattr(self, "group_active_group_var", None)
        label = var.get() if var is not None else ""
        return getattr(self, "_group_selector_id_by_label", {}).get(label)

    def _remove_group_import_group(self):
        group_id = self._selected_group_import_group_id()
        if not group_id:
            messagebox.showinfo(
                self._group_text("Gruppeimport", "Group import"),
                self._group_text("Vælg en gruppe, der skal fjernes.", "Select a group to remove."),
                parent=getattr(self, "group_import_window", self),
            )
            return
        group = None
        for item in getattr(self, "group_import_groups", []) or []:
            if (item.get("id") or item.get("name")) == group_id:
                group = item
                break
        if group is None:
            return
        group_name = str(group.get("name", "") or "")
        self.group_import_groups = [
            item for item in (getattr(self, "group_import_groups", []) or [])
            if (item.get("id") or item.get("name")) != group_id
        ]
        for player in getattr(self, "group_import_players", []) or []:
            if str(player.get("assigned_group", "") or "") == group_name:
                player["assigned_group"] = ""
                player["selected"] = False
        active_id = (getattr(self, "active_group", None) or {}).get("id")
        active_removed = active_id == group_id
        if active_removed:
            # 3.60: Fjerner man den aktive gruppe, skal Kontrolcenter ryddes på
            # samme måde som Ryd-knappen i spillersektionen.
            self._reset_main_workspace()
        self._refresh_group_selector()
        try:
            self._refresh_presets_menu()
        except Exception:
            pass
        self._group_refresh_tree(apply_sort=False)
        self._refresh_group_report_window()
        if hasattr(self, "group_import_status_var"):
            self.group_import_status_var.set(self._group_text(f"Gruppen '{group_name}' er fjernet.", f"Group '{group_name}' removed."))

    def _group_unassign_player_from_group(self, player):
        """Remove a single player from its assigned Group import group."""
        if not player:
            return
        group_name = str(player.get("assigned_group", "") or "").strip()
        if not group_name:
            return
        player_key = self._group_player_key(player)
        changed_group_id = None
        new_groups = []
        for group in (getattr(self, "group_import_groups", []) or []):
            if str(group.get("name", "") or "") != group_name:
                new_groups.append(group)
                continue
            group = copy.deepcopy(group)
            changed_group_id = group.get("id") or group.get("name")
            group["players"] = [p for p in (group.get("players", []) or []) if self._group_player_key(p) != player_key]
            if group.get("players"):
                rebuilt = self._build_group_from_players(group.get("name", group_name), group.get("players", []), group.get("id"))
                new_groups.append(rebuilt or group)
        self.group_import_groups = new_groups
        player["assigned_group"] = ""
        player["selected"] = False

        active = getattr(self, "active_group", None)
        if active and (str(active.get("name", "") or "") == group_name or (changed_group_id and active.get("id") == changed_group_id)):
            remaining = [p for p in (active.get("players", []) or []) if self._group_player_key(p) != player_key]
            if remaining:
                rebuilt = self._build_group_from_players(active.get("name", group_name), remaining, active.get("id"))
                if rebuilt:
                    self.active_group = rebuilt
                    self._activate_group_data_to_main(copy.deepcopy(rebuilt), undo_label=self._group_text("Gruppe ændret", "Group changed"))
            else:
                self.active_group = None
                self._stop_group_avatar_rotation()
                self._set_player_avatar_placeholder()
                self._set_group_report_button_visible(False)

        self._refresh_group_selector((getattr(self, "active_group", None) or {}).get("id"))
        self._group_refresh_tree(apply_sort=False)
        self._refresh_group_report_window()
        if hasattr(self, "group_import_status_var"):
            self.group_import_status_var.set(self._group_text(f"{player.get('name', 'Spilleren')} er fjernet fra gruppen '{group_name}'.", f"{player.get('name', 'The player')} was removed from group '{group_name}'."))

    def _register_group_import_group(self, group):
        if not group:
            return
        if not hasattr(self, "group_import_groups"):
            self.group_import_groups = []
        group_id = group.get("id") or group.get("name") or str(time.time())
        stored = copy.deepcopy(group)
        for idx, existing in enumerate(self.group_import_groups):
            if (existing.get("id") or existing.get("name")) == group_id:
                self.group_import_groups[idx] = stored
                self._refresh_group_selector(group_id)
                try:
                    self._refresh_presets_menu()
                except Exception:
                    pass
                return
        self.group_import_groups.append(stored)
        self._refresh_group_selector(group_id)
        try:
            self._refresh_presets_menu()
        except Exception:
            pass

    def _build_group_from_players(self, name, players, group_id=None):
        position = self._group_target_position_for_players(players)
        stats = POSITION_STATS[position]
        valid_players = [p for p in players if self._group_player_has_xp(p) and all(stat in (p.get("stats") or {}) for stat in stats)]
        if not valid_players:
            return None

        def avg(values):
            clean = [float(v) for v in values if v is not None]
            return sum(clean) / len(clean) if clean else None

        avg_stats = {stat: avg([(p.get("stats") or {}).get(stat) for p in valid_players]) for stat in stats}
        avg_age = avg([p.get("age") for p in valid_players])
        avg_xp = avg([p.get("xp") for p in valid_players])
        int_stats = {stat: max(0, min(100, int(round(avg_stats[stat])))) for stat in stats}
        state = PlayerState(stats=int_stats, stat_names=stats)
        avg_rating = calculate_rating(state, position, stat_mode="fractional")
        return {
            "id": group_id or f"{name}-{int(time.time() * 1000)}",
            "name": name,
            "players": copy.deepcopy(valid_players),
            "position": position,
            "avg_age": avg_age,
            "avg_xp": avg_xp,
            "avg_stats": avg_stats,
            "avg_rating": avg_rating,
        }

    def _reset_assistant_for_active_group(self):
        self._assistant_reset_from_main_window("Assistenten er nulstillet efter ændringer i Spiller-sektionen.")


    def _group_identifier(self, group):
        if not isinstance(group, dict):
            return None
        return group.get("id") or group.get("name")

    def _store_active_group_program_snapshot(self):
        """Store the current Control Center program on the active group.

        This makes group switching stateful: each imported group carries its
        own training program instead of all groups sharing whatever happens to
        be visible in the Control Center at the moment.
        """
        if getattr(self, "_suppress_group_program_store", False):
            return
        group = getattr(self, "active_group", None)
        if not isinstance(group, dict) or not group:
            return
        group_id = self._group_identifier(group)
        if not group_id:
            return
        try:
            program = list(getattr(self, "program", []) or [])
            snapshot = self._group_report_serialize_program(program)
            program_days = self._group_report_program_days(program)
        except Exception:
            snapshot = []
            program_days = 0
        source = "group_program" if snapshot else "group_empty"
        group["program_snapshot"] = copy.deepcopy(snapshot)
        group["program_source"] = source
        group["program_days"] = program_days
        try:
            for existing in getattr(self, "group_import_groups", []) or []:
                if self._group_identifier(existing) == group_id:
                    existing["program_snapshot"] = copy.deepcopy(snapshot)
                    existing["program_source"] = source
                    existing["program_days"] = program_days
                    break
        except Exception:
            pass
        try:
            for entry in getattr(self, "group_report_history", []) or []:
                if self._group_identifier(entry) == group_id:
                    entry["program_snapshot"] = copy.deepcopy(snapshot)
                    entry["program_source"] = source
                    entry["program_days"] = program_days
                    break
        except Exception:
            pass

    def _load_group_program_to_main(self, group):
        """Load the selected group's own program into the Control Center."""
        program = []
        try:
            if isinstance(group, dict) and "program_snapshot" in group:
                program = self._group_report_deserialize_program(group.get("program_snapshot") or [])
            else:
                position = internal_position((group or {}).get("position", self.position_var.get()))
                program, _source = self._group_report_builtin_program_for_position(position)
        except Exception:
            program = []
        self.program = copy.deepcopy(program)
        self.clipboard_phases = []
        self.editing_index = None
        try:
            self._refresh_program_tree()
        except Exception:
            pass
        try:
            name = str((group or {}).get("name", "Gruppe") or "Gruppe")
            self._set_template_name(name, dirty=False)
        except Exception:
            pass

    def _activate_group_data_to_main(self, group, undo_label="Gruppeimport"):
        if not group:
            return False
        try:
            self._store_active_group_program_snapshot()
        except Exception:
            pass
        position = self._group_target_position_for_players(group.get("players", []))
        stats = POSITION_STATS[position]
        avg_stats = dict(group.get("avg_stats") or {})
        if not avg_stats or any(stat not in avg_stats for stat in stats):
            rebuilt = self._build_group_from_players(group.get("name", "Gruppe"), group.get("players", []), group.get("id"))
            if not rebuilt:
                self._main_notice(self._group_text("Ingen af de valgte spillere har komplette egenskaber til den valgte målposition.", "None of the selected players has complete abilities for the chosen target position."))
                return False
            group = rebuilt
            position = group.get("position", position)
            stats = POSITION_STATS[position]
            avg_stats = dict(group.get("avg_stats") or {})
        int_stats = {stat: max(0, min(100, int(round(float(avg_stats[stat]))))) for stat in stats}

        self._push_undo(undo_label)
        self._undo_suspended = True
        previous_group_program_store = getattr(self, "_suppress_group_program_store", False)
        previous_template_dirty_suppression = getattr(self, "_suppress_template_dirty", False)
        self._suppress_group_program_store = True
        self._suppress_template_dirty = True
        try:
            self.active_group = copy.deepcopy(group)
            self.position_var.set(self._display_position(position))
            self.current_stats = POSITION_STATS[position]
            self._on_position_change(initial=False)
            if group.get("avg_age") is not None:
                self._set_main_start_age_decimal(group.get("avg_age"))
            if group.get("avg_xp") is not None:
                imported_xp = self._format_imported_training_xp(group.get("avg_xp"))
                if imported_xp is not None:
                    self.xp_var.set(imported_xp)
            for stat in stats:
                if stat in self.start_stat_vars:
                    self.start_stat_vars[stat].set(str(int_stats[stat]))
            self._player_link_raw_value = ""
            self._player_link_display_value = f"{self._group_text('Aktiv gruppe', 'Active group')}: {group.get('name', 'Gruppe')} ({len(group.get('players', []) or [])})"
            self._player_link_display_mode = "display"
            self.player_link_var.set(self._player_link_display_value)
            self._load_group_program_to_main(self.active_group)
            self._set_group_report_button_visible(True)
            self._settings_changed()
        finally:
            self._suppress_group_program_store = previous_group_program_store
            self._suppress_template_dirty = previous_template_dirty_suppression
            self._undo_suspended = False
        try:
            self._store_active_group_program_snapshot()
        except Exception:
            pass
        self._register_group_import_group(self.active_group)
        self._reset_assistant_for_active_group()
        self._start_group_avatar_rotation()
        self._add_group_report_entry(simulated=False)
        self._schedule_group_report_recompute(delay=80)
        self._refresh_group_report_window()
        self._refresh_group_selector((self.active_group or {}).get("id"))
        return True

    def _activate_group_from_import_selector(self):
        var = getattr(self, "group_active_group_var", None)
        label = var.get() if var is not None else ""
        group_id = getattr(self, "_group_selector_id_by_label", {}).get(label)
        if not group_id:
            return
        for group in getattr(self, "group_import_groups", []) or []:
            if (group.get("id") or group.get("name")) == group_id:
                self._activate_group_data_to_main(copy.deepcopy(group), undo_label=self._group_text("Aktiv gruppe", "Active group"))
                self._group_refresh_tree()
                return

    def _stop_group_avatar_rotation(self):
        try:
            after_id = getattr(self, "_group_avatar_rotation_after_id", None)
            if after_id is not None:
                self.after_cancel(after_id)
        except Exception:
            pass
        self._group_avatar_rotation_after_id = None
        self._group_avatar_rotation_generation = int(getattr(self, "_group_avatar_rotation_generation", 0) or 0) + 1
        self._group_avatar_rotation_index = -1
        self._group_avatar_photo_cache = {}

    def _start_group_avatar_rotation(self):
        group = getattr(self, "active_group", None)
        if not group or not group.get("players"):
            self._stop_group_avatar_rotation()
            self._set_player_avatar_placeholder()
            return
        self._stop_group_avatar_rotation()
        generation = int(getattr(self, "_group_avatar_rotation_generation", 0) or 0)
        self._group_avatar_photo_cache = {}
        self._group_avatar_rotation_keys = []
        q = queue.Queue()
        self._group_avatar_fetch_queue = q
        players = copy.deepcopy(group.get("players", []) or [])
        self._group_avatar_debug_records = []
        self._group_avatar_debug_summary = {
            "group_id": group.get("id"),
            "group_name": group.get("name"),
            "player_count": len(players),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        # Show already-known portraits immediately, then fetch the missing ones.
        for player in players:
            key = self._group_player_key(player)
            if not key or key in self._group_avatar_photo_cache:
                continue
            image_bytes = player.get("avatar_image_bytes")
            if image_bytes:
                try:
                    photo = self._make_avatar_photo(image_bytes, target_height=int(getattr(self, "_player_avatar_image_height", 110)))
                except Exception:
                    photo = None
                if photo is not None:
                    self._group_avatar_photo_cache[key] = photo
                    self._group_avatar_rotation_keys.append(key)
        if not self._group_avatar_photo_cache:
            self._set_player_avatar_placeholder()

        def fetch_one(player):
            key = self._group_player_key(player)
            if not key:
                return None
            image_bytes = player.get("avatar_image_bytes")
            avatar_url = player.get("avatar_url")
            content_type = player.get("avatar_content_type")
            if not image_bytes:
                image_bytes, avatar_url, content_type = self._fetch_group_player_avatar_only(player)
            return (key, image_bytes, avatar_url, content_type)

        def worker():
            try:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                max_workers = max(1, min(10, len(players)))
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(fetch_one, player) for player in players]
                    for future in as_completed(futures):
                        try:
                            result = future.result()
                        except Exception:
                            result = None
                        if result:
                            key, image_bytes, avatar_url, content_type = result
                            q.put((generation, key, image_bytes, avatar_url, content_type))
            except Exception:
                for player in players:
                    result = fetch_one(player)
                    if result:
                        key, image_bytes, avatar_url, content_type = result
                        q.put((generation, key, image_bytes, avatar_url, content_type))
            q.put((generation, "__done__", None, None, None))

        threading.Thread(target=worker, daemon=True).start()
        self.after(80, self._drain_group_avatar_fetch_queue)
        self._group_avatar_rotate_once()

    def _drain_group_avatar_fetch_queue(self):
        q = getattr(self, "_group_avatar_fetch_queue", None)
        if q is None:
            return
        generation = int(getattr(self, "_group_avatar_rotation_generation", 0) or 0)
        done = False
        processed = 0
        changed = False
        try:
            while processed < 20:
                item = q.get_nowait()
                processed += 1
                if len(item) == 5:
                    item_generation, key, image_bytes, avatar_url, content_type = item
                else:
                    item_generation, key, image_bytes, avatar_url = item
                    content_type = None
                if item_generation != generation:
                    continue
                if key == "__done__":
                    done = True
                    continue
                if image_bytes:
                    try:
                        photo = self._make_avatar_photo(image_bytes, target_height=int(getattr(self, "_player_avatar_image_height", 110)))
                    except Exception:
                        photo = None
                    if photo is not None:
                        cache = getattr(self, "_group_avatar_photo_cache", {})
                        if key not in cache:
                            self._group_avatar_rotation_keys.append(key)
                        cache[key] = photo
                        self._group_avatar_photo_cache = cache
                        changed = True
                        active = getattr(self, "active_group", None)
                        if active:
                            for player in active.get("players", []) or []:
                                if self._group_player_key(player) == key:
                                    player["avatar_image_bytes"] = image_bytes
                                    player["avatar_url"] = avatar_url
                                    player["avatar_content_type"] = content_type
                                    break
        except queue.Empty:
            pass
        if changed and getattr(self, "_group_avatar_rotation_after_id", None) is None:
            self._group_avatar_rotate_once()
        if not done:
            self.after(80, self._drain_group_avatar_fetch_queue)

    def _sync_assistant_avatar_photo(self, photo):
        """Mirror the active group portrait into Assistant step 1.

        Step 1 used to copy the current Control Center portrait only once. The
        Control Center then kept rotating every two seconds, while the Assistant
        remained frozen. This helper updates the existing Assistant canvas on
        each normal rotation tick without starting a second timer.
        """
        canvas = getattr(self, "assistant_avatar_canvas", None)
        if canvas is None or photo is None:
            return
        try:
            if int(getattr(self, "assistant_step", -1)) != 0:
                return
            if not canvas.winfo_exists():
                return
            self._draw_avatar_photo_on_canvas(canvas, photo)
        except Exception:
            pass

    def _group_avatar_rotate_once(self):
        group = getattr(self, "active_group", None)
        if not group or not group.get("players"):
            self._group_avatar_rotation_after_id = None
            return
        cache = getattr(self, "_group_avatar_photo_cache", {}) or {}
        order = list(getattr(self, "_group_avatar_rotation_keys", []) or [])
        if not order:
            players = group.get("players", []) or []
            order = [self._group_player_key(p) for p in players]
        keys = []
        seen = set()
        for key in order:
            if key in cache and key not in seen:
                seen.add(key)
                keys.append(key)
        if keys:
            try:
                current_index = int(getattr(self, "_group_avatar_rotation_index", -1))
            except Exception:
                current_index = -1
            self._group_avatar_rotation_index = (current_index + 1) % len(keys)
            key = keys[self._group_avatar_rotation_index]
            self._group_avatar_current_key = key
            self.player_avatar_photo = cache[key]
            self._place_player_avatar_photo(cache[key])
            self._sync_assistant_avatar_photo(cache[key])
        try:
            self._group_avatar_rotation_after_id = self.after(2000, self._group_avatar_rotate_once)
        except Exception:
            self._group_avatar_rotation_after_id = None

    def _group_add_selected_to_main(self):
        selected = self._selected_group_players()
        if not selected:
            messagebox.showinfo(
                self._group_text("Gruppeimport", "Group import"),
                self._group_text("Vælg mindst én spiller.", "Select at least one player."),
                parent=getattr(self, "group_import_window", self),
            )
            return
        already_assigned = [p for p in selected if str(p.get("assigned_group", "") or "").strip()]
        if already_assigned:
            names = ", ".join(str(p.get("name", "")) for p in already_assigned[:5])
            extra = "…" if len(already_assigned) > 5 else ""
            self._main_notice(
                self._group_text(
                    f"{len(already_assigned)} spiller(e) er allerede tilføjet til en gruppe: {names}{extra}",
                    f"{len(already_assigned)} player(s) already belong to a group: {names}{extra}",
                )
            )
            return
        name = simpledialog.askstring(
            self._group_text("Gruppenavn", "Group name"),
            self._group_text("Navngiv gruppen:", "Name the group:"),
            parent=self.group_import_window,
        )
        if not name:
            return
        self._apply_group_to_main(name.strip(), selected)

    def _apply_group_to_main(self, name, players):
        players = [p for p in (players or []) if self._group_player_has_xp(p)]
        if not players:
            self._main_notice(self._group_text("Ingen af de valgte spillere har XP og kan derfor ikke tilføjes til en gruppe.", "None of the selected players has XP, so they cannot be added to a group."))
            return
        group = self._build_group_from_players(name, players)
        if group is None:
            self._main_notice(self._group_text("Ingen af de valgte spillere har komplette egenskaber til den valgte målposition.", "None of the selected players has complete abilities for the chosen target position."))
            return
        for player in players:
            if self._group_player_key(player) in {self._group_player_key(p) for p in group.get("players", [])}:
                player["assigned_group"] = name
        if self._activate_group_data_to_main(group, undo_label="Gruppeimport"):
            self._group_refresh_tree()

    def _group_report_program_days(self, program):
        try:
            return len(expand_program(program or []))
        except Exception:
            try:
                return sum(int(getattr(item, "days", 0) or 0) for item in program or [])
            except Exception:
                return 0

    def _group_report_serialize_program(self, program):
        try:
            return [self._serialize_program_item(item) for item in (program or [])]
        except Exception:
            return []

    def _group_report_deserialize_program(self, payload):
        program = []
        for item in payload or []:
            try:
                if isinstance(item, (TrainingInstruction, TrainingCycle)):
                    program.append(copy.deepcopy(item))
                elif isinstance(item, dict):
                    program.append(self._deserialize_program_item(item))
            except Exception:
                continue
        return program

    def _group_report_builtin_program_for_position(self, position):
        try:
            position = internal_position(position)
            preset_name = self._sheikh_template_for_position(position)
            data = BUILTIN_PRESETS.get(preset_name) if preset_name else None
            if not data:
                return [], "none"
            program = [self._deserialize_program_item(item) for item in data.get("program", [])]
            if program:
                return program, f"template:{preset_name}"
        except Exception as error:
            try:
                self._group_report_last_errors = list(getattr(self, "_group_report_last_errors", []) or []) + [f"Skabelonprogram: {error}"]
            except Exception:
                pass
        return [], "none"

    def _assistant_selected_or_best_program_for_report(self):
        try:
            result = None
            tree = getattr(self, "assistant_top_tree", None)
            result_map = getattr(self, "assistant_tree_result_map", {}) or {}
            if tree is not None and tree.winfo_exists():
                try:
                    selection = tree.selection()
                    if selection:
                        result = result_map.get(selection[0])
                except Exception:
                    pass
            source = "assistant_selected"
            if result is None:
                results = self._assistant_all_results()
                if results:
                    result = results[0]
                    source = "assistant_best"
            if isinstance(result, dict):
                result = self._assistant_result_from_dict(result)
            program = list(getattr(result, "program", []) or []) if result is not None else []
            if program:
                return program, source
        except Exception as error:
            try:
                self._group_report_last_errors = list(getattr(self, "_group_report_last_errors", []) or []) + [f"Assistentprogram: {error}"]
            except Exception:
                pass
        return [], "none"

    def _group_report_program(self, group=None):
        """Return the program for a group report entry.

        3.95: The report is parallel. Each group stores/uses its own program
        snapshot. The current UI/Assistant state is only allowed for the active
        group. Existing Control Center layout/state is otherwise left alone.
        """
        source_group = group or getattr(self, "active_group", None) or {}

        # Already stored group-specific program snapshot wins, also when the
        # group deliberately has an empty program. Empty means baseline report;
        # it must not fall back to another group's current Control Center program.
        try:
            if isinstance(source_group, dict) and "program_snapshot" in source_group:
                program = self._group_report_deserialize_program(source_group.get("program_snapshot") or [])
                return program, source_group.get("program_source", "group_snapshot")
        except Exception:
            pass

        try:
            group_id = source_group.get("id") or source_group.get("name")
            active = getattr(self, "active_group", None) or {}
            active_id = active.get("id") or active.get("name")
            allow_current_ui = (group is None) or (bool(group_id) and bool(active_id) and group_id == active_id)
        except Exception:
            allow_current_ui = True

        if allow_current_ui:
            program, source = self._assistant_selected_or_best_program_for_report()
            if program:
                return program, source
            try:
                if getattr(self, "program", None):
                    return list(self.program), "main_program"
            except Exception:
                pass

        try:
            position = internal_position(source_group.get("position", self.position_var.get()))
            program, source = self._group_report_builtin_program_for_position(position)
            if program:
                return program, source
        except Exception:
            pass

        return [], "none"

    def _group_report_compute_args(self, group=None):
        group = copy.deepcopy(group or getattr(self, "active_group", None) or {})
        try:
            main_xp = float(self.xp_var.get())
        except Exception:
            main_xp = 0.0
        try:
            start_age_default = float(self.start_age_var.get())
        except Exception:
            start_age_default = 0.0
        program, program_source = self._group_report_program(group)
        program_snapshot = self._group_report_serialize_program(program)
        program_days = self._group_report_program_days(program)
        self._group_report_program_source = program_source
        return {
            "group": group,
            "program": copy.deepcopy(program),
            "program_snapshot": program_snapshot,
            "program_source": program_source,
            "program_days": program_days,
            "main_xp": main_xp,
            "start_age_default": start_age_default,
            "reference_training_points": self._get_xp_reference_training_points(),
            "experience_bonus": self._get_experience_bonus_settings(),
        }

    def _compute_group_report_rows(self, args):
        group = args.get("group") or {}
        program = args.get("program") or []
        if not group:
            return [], []
        if not program:
            return self._group_report_baseline_rows(group), []
        try:
            target_position = internal_position(group.get("position", "Forsvar"))
        except Exception:
            target_position = "Forsvar"
        try:
            main_xp = float(args.get("main_xp", 0.0))
        except Exception:
            main_xp = 0.0
        try:
            start_age_default = float(args.get("start_age_default", 0.0))
        except Exception:
            start_age_default = 0.0
        try:
            reference_training_points = int(args.get("reference_training_points", 23))
        except Exception:
            reference_training_points = 23
        try:
            ebr_enabled, ebr_ratio, ebr_knee = args.get("experience_bonus", (False, 1.10, "linear"))
        except Exception:
            ebr_enabled, ebr_ratio, ebr_knee = False, 1.10, "linear"
        try:
            program_days = int(args.get("program_days", self._group_report_program_days(program)) or 0)
        except Exception:
            program_days = 0

        rows = []
        errors = []
        seen_keys = set()
        for player in group.get("players", []):
            key = self._group_player_key(player)
            if key and key in seen_keys:
                continue
            if key:
                seen_keys.add(key)
            player_name = player.get("name", "Ukendt")
            player_position = target_position
            try:
                player_position = internal_position(player.get("engine_position", target_position))
                stats = POSITION_STATS[player_position]
                pstats = player.get("stats") or {}
                missing_stats = [stat for stat in stats if stat not in pstats]
                if missing_stats:
                    errors.append(f"{player_name}: mangler egenskaber for {player_position}: {', '.join(missing_stats)}")
                    continue

                start_stats = {stat: max(0, min(100, int(round(float(pstats[stat]))))) for stat in stats}
                start_age = float(player.get("age") if player.get("age") is not None else start_age_default)
                player_xp = player.get("xp")
                xp = float(player_xp) if player_xp is not None else main_xp
                final_state, _history = simulate_program(
                    start_stats=start_stats,
                    program=program,
                    position=player_position,
                    xp=xp,
                    start_age=start_age,
                    keep_history=False,
                    reference_training_points=reference_training_points,
                    experience_bonus_enabled=ebr_enabled,
                    experience_bonus_ratio=ebr_ratio,
                    experience_bonus_knee=ebr_knee,
                    experience_bonus_start_age=start_age,
                )
                summary = summarize_state(final_state, position=player_position)
                start_rating = self._group_player_start_rating(player, player_position)
                if start_rating is None:
                    start_rating = player.get("rating")
                try:
                    start_rating = float(start_rating) if start_rating is not None else None
                except Exception:
                    start_rating = None
                final_rating = None
                try:
                    final_rating = float(summary.get(f"Vurderingstal_{player_position}"))
                except Exception:
                    try:
                        final_rating = calculate_rating(final_state, player_position, stat_mode="fractional")
                    except Exception as error:
                        errors.append(f"{player_name}: kunne ikke beregne slut-VT: {error}")
                final_average = None
                try:
                    final_average = float(summary.get("Gennemsnit"))
                except Exception:
                    try:
                        final_average = final_state.average_stat(mode="fractional")
                    except Exception as error:
                        errors.append(f"{player_name}: kunne ikke beregne gennemsnit: {error}")
                rows.append({
                    "key": key,
                    "url": player.get("url", ""),
                    "name": player_name,
                    "start_age": start_age,
                    "final_age": calculate_end_age(start_age, program_days),
                    "position": player_position,
                    "target_position": target_position,
                    "start_rating": start_rating,
                    "final_rating": final_rating,
                    "delta": None if start_rating is None or final_rating is None else final_rating - start_rating,
                    "average": final_average,
                })
            except Exception as error:
                errors.append(f"{player_name}: {error}")
        return rows, errors

    def _simulate_group_players_for_report(self):
        args = self._group_report_compute_args()
        rows, errors = self._compute_group_report_rows(args)
        self._group_report_last_errors = errors
        return rows

    def _schedule_group_report_recompute(self, delay=350):
        if not getattr(self, "active_group", None):
            return
        try:
            after_id = getattr(self, "_group_report_after_id", None)
            if after_id is not None:
                self.after_cancel(after_id)
        except Exception:
            pass
        try:
            self._group_report_after_id = self.after(int(delay), self._start_group_report_recompute)
        except Exception:
            self._start_group_report_recompute()

    def _start_group_report_recompute(self):
        self._group_report_after_id = None
        group = getattr(self, "active_group", None)
        if not group:
            return
        self._add_group_report_entry(simulated=False)
        self._refresh_group_report_window()
        self._group_report_compute_generation = int(getattr(self, "_group_report_compute_generation", 0) or 0) + 1
        generation = self._group_report_compute_generation
        args = self._group_report_compute_args(group)
        q = queue.Queue()
        self._group_report_compute_queue = q
        group_id = args.get("group", {}).get("id") or args.get("group", {}).get("name")
        if hasattr(self, "group_report_status_var"):
            self.group_report_status_var.set(self._group_text("Beregner gruppe-rapport…", "Calculating group report…"))

        def worker():
            try:
                rows, errors = self._compute_group_report_rows(args)
            except Exception as error:
                rows, errors = [], [str(error)]
            q.put((generation, group_id, rows, errors, args.get("program_snapshot", []), args.get("program_source", "none"), args.get("program_days", 0)))

        threading.Thread(target=worker, daemon=True).start()
        self.after(80, self._drain_group_report_compute_queue)

    def _drain_group_report_compute_queue(self):
        q = getattr(self, "_group_report_compute_queue", None)
        if q is None:
            return
        generation = int(getattr(self, "_group_report_compute_generation", 0) or 0)
        try:
            item = q.get_nowait()
            if len(item) >= 7:
                item_generation, group_id, rows, errors, program_snapshot, program_source, program_days = item[:7]
            else:
                item_generation, group_id, rows, errors = item
                program_snapshot, program_source, program_days = [], "none", 0
        except queue.Empty:
            self.after(80, self._drain_group_report_compute_queue)
            return
        if item_generation != generation:
            return
        self._group_report_last_errors = list(errors or [])
        existing = None
        for entry in self.group_report_history:
            if entry.get("id") == group_id:
                existing = entry
                break
        if existing is not None:
            # Keep a single row per player in this report entry.
            clean_rows = []
            seen = set()
            for row in rows or []:
                key = row.get("key") or str(row.get("url", "")) or row.get("name", "")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                clean_rows.append(row)

            # 3.92: Hvis beregningen kun returnerer nogle af spillerne — eller
            # ingen rows — må resten ikke forsvinde fra Grupperapport. Udfyld
            # manglende spillere med baseline-rækker fra importdata.
            try:
                entry_position = internal_position(existing.get("position", "Forsvar"))
            except Exception:
                entry_position = "Forsvar"
            for player in existing.get("players", []) or []:
                key = self._group_player_key(player)
                if key and key in seen:
                    continue
                baseline = self._group_report_baseline_rows({"position": entry_position, "players": [player]})
                if baseline:
                    row = baseline[0]
                    clean_rows.append(row)
                    row_key = row.get("key") or key
                    if row_key:
                        seen.add(row_key)

            existing.update({
                "simulated": True,
                "rows": clean_rows,
                "program_snapshot": copy.deepcopy(program_snapshot),
                "program_source": program_source,
                "program_days": program_days,
            })
        self._refresh_group_report_window()

    def _add_group_report_entry(self, simulated=False):
        group = getattr(self, "active_group", None)
        if not group:
            return
        name = group.get("name", "Gruppe")
        group_id = group.get("id") or name
        program, program_source = self._group_report_program(group)
        program_snapshot = self._group_report_serialize_program(program)
        program_days = self._group_report_program_days(program)
        rows = self._simulate_group_players_for_report() if simulated else []
        baseline_rows = [] if simulated else self._group_report_baseline_rows(group)

        # One report entry per active group. Opening the report window should not
        # append the same players again, and a player should only appear once in
        # the accumulated report.
        existing = None
        for entry in self.group_report_history:
            if entry.get("id") == group_id:
                existing = entry
                break
        if existing is None:
            report_player_keys = set()
            for entry in self.group_report_history:
                for player in entry.get("players", []):
                    report_player_keys.add(self._group_player_key(player))
                for row in entry.get("rows", []):
                    if row.get("key"):
                        report_player_keys.add(row.get("key"))
            players = []
            for player in copy.deepcopy(group.get("players", [])):
                key = self._group_player_key(player)
                if key and key in report_player_keys:
                    continue
                players.append(player)
            existing = {
                "id": group_id,
                "name": name,
                "simulated": False,
                "position": group.get("position"),
                "players": players,
                "rows": [],
                "program_snapshot": copy.deepcopy(program_snapshot),
                "program_source": program_source,
                "program_days": program_days,
            }
            if baseline_rows:
                existing["rows"] = [row for row in baseline_rows if (not players) or row.get("key") in {self._group_player_key(p) for p in players}]
                existing["simulated"] = True
            self.group_report_history.append(existing)
        else:
            if program_snapshot:
                existing["program_snapshot"] = copy.deepcopy(program_snapshot)
                existing["program_source"] = program_source
                existing["program_days"] = program_days
        if simulated:
            report_player_keys = set()
            for entry in self.group_report_history:
                if entry is existing:
                    continue
                for player in entry.get("players", []):
                    report_player_keys.add(self._group_player_key(player))
                for row in entry.get("rows", []):
                    if row.get("key"):
                        report_player_keys.add(row.get("key"))
            allowed_keys = {self._group_player_key(player) for player in existing.get("players", [])}
            clean_rows = []
            for row in rows:
                key = row.get("key") or str(row.get("url", "")) or row.get("name", "")
                if allowed_keys and key not in allowed_keys:
                    continue
                if key and key in report_player_keys:
                    continue
                clean_rows.append(row)
            existing.update({
                "simulated": True,
                "rows": clean_rows,
                "program_snapshot": copy.deepcopy(program_snapshot),
                "program_source": program_source,
                "program_days": program_days,
            })

    def _show_group_report_window(self):
        if self.group_report_window is not None and self.group_report_window.winfo_exists():
            self.group_report_window.lift()
            self._refresh_group_report_window()
            return
        win = tk.Toplevel(self)
        self.group_report_window = win
        win.title(self._group_text("Grupperapport", "Group report"))
        win.geometry("1130x620")
        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)

        columns = ("group", "player", "position", "start_age", "final_age", "start_rating", "final_rating", "delta", "average")
        self.group_report_tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        headings = {
            "group": self._group_text("Gruppe", "Group"),
            "player": self._group_text("Spiller", "Player"),
            "position": self._group_text("Position", "Position"),
            "start_age": self._group_text("Startalder", "Start age"),
            "final_age": self._group_text("Slutalder", "End age"),
            "start_rating": self._group_text("Start-VT", "Start rating"),
            "final_rating": self._group_text("Slut-VT", "Final rating"),
            "delta": "ΔVT" if self._language_code() != "en" else "ΔRating",
            "average": self._group_text("Gennemsnit", "Average"),
        }
        for col in columns:
            self.group_report_tree.heading(col, text=headings[col], command=lambda c=col: self._sort_group_report_tree(c))
        self.group_report_tree.column("group", width=150, anchor="w", stretch=False)
        self.group_report_tree.column("player", width=240, anchor="w", stretch=True)
        self.group_report_tree.column("position", width=110, anchor="center", stretch=False)
        self.group_report_tree.column("start_age", width=90, anchor="center", stretch=False)
        self.group_report_tree.column("final_age", width=90, anchor="center", stretch=False)
        self.group_report_tree.column("start_rating", width=100, anchor="center", stretch=False)
        self.group_report_tree.column("final_rating", width=100, anchor="center", stretch=False)
        self.group_report_tree.column("delta", width=90, anchor="center", stretch=False)
        self.group_report_tree.column("average", width=100, anchor="center", stretch=False)

        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.group_report_tree.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.group_report_tree.xview)
        self.group_report_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.group_report_tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        bottom = ttk.Frame(frame)
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        bottom.grid_columnconfigure(0, weight=1)
        self.group_report_status_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.group_report_status_var).grid(row=0, column=0, sticky="w")
        self.group_report_export_button = ttk.Menubutton(
            bottom,
            text=self._group_text("Eksportér", "Export"),
        )
        self.group_report_export_menu = tk.Menu(self.group_report_export_button, tearoff=0)
        self.group_report_export_menu.add_command(
            label="PDF",
            command=self._export_group_report_pdf,
        )
        self.group_report_export_menu.add_command(
            label="DOC",
            command=self._export_group_report_doc,
        )
        self.group_report_export_button["menu"] = self.group_report_export_menu
        self.group_report_export_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        ttk.Button(
            bottom,
            text=self._group_text("Fjern valgte", "Remove selected"),
            command=self._remove_selected_from_group_report,
        ).grid(row=0, column=2, sticky="e", padx=(8, 0))
        ttk.Button(
            bottom,
            text=self._group_text("Fjern gruppe", "Remove group"),
            command=self._remove_selected_group_from_report,
        ).grid(row=0, column=3, sticky="e", padx=(8, 0))
        self._refresh_group_report_window()
        try:
            program_for_report, _source = self._group_report_program()
            if getattr(self, "active_group", None):
                self._schedule_group_report_recompute(delay=80)
        except Exception:
            pass

    def _group_report_rows(self):
        rows = []
        for entry in self.group_report_history:
            group_name = entry.get("name", "Group")
            group_id = entry.get("id") or group_name
            position = internal_position(entry.get("position", "Forsvar"))
            if entry.get("simulated"):
                for row in entry.get("rows", []):
                    key = row.get("key") or str(row.get("url", "")) or row.get("name", "")
                    start_rating = row.get("start_rating")
                    final_rating = row.get("final_rating")
                    delta = row.get("delta")
                    average = row.get("average")
                    if final_rating is None:
                        final_rating = start_rating
                    if delta is None and start_rating is not None and final_rating is not None:
                        try:
                            delta = float(final_rating) - float(start_rating)
                        except Exception:
                            delta = 0.0
                    if average is None:
                        try:
                            for player in entry.get("players", []) or []:
                                if self._group_player_key(player) == key:
                                    pstats = player.get("stats") or {}
                                    pos = internal_position(player.get("engine_position", position))
                                    stats = POSITION_STATS[pos]
                                    vals = [float(pstats[stat]) for stat in stats if stat in pstats]
                                    if vals:
                                        average = sum(vals) / len(vals)
                                    break
                        except Exception:
                            pass
                    rows.append({
                        "group_id": group_id,
                        "key": key,
                        "group": group_name,
                        "player": row.get("name", ""),
                        "position": self._display_position(row.get("position", position)),
                        "start_age": row.get("start_age"),
                        "final_age": row.get("final_age", row.get("start_age")),
                        "start_rating": start_rating,
                        "final_rating": final_rating,
                        "delta": delta,
                        "average": average,
                    })
            else:
                for player in entry.get("players", []):
                    baseline = self._group_report_baseline_rows({"position": position, "players": [player]})
                    row = baseline[0] if baseline else {}
                    key = row.get("key") or self._group_player_key(player)
                    rows.append({
                        "group_id": group_id,
                        "key": key,
                        "group": group_name,
                        "player": player.get("name", ""),
                        "position": self._display_position(player.get("engine_position", position)),
                        "start_age": player.get("age"),
                        "final_age": row.get("final_age", row.get("start_age", player.get("age"))),
                        "start_rating": row.get("start_rating"),
                        "final_rating": row.get("final_rating"),
                        "delta": row.get("delta"),
                        "average": row.get("average"),
                    })
        return rows

    def _sort_group_report_tree(self, col, reverse=None):
        tree = getattr(self, "group_report_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        if reverse is None:
            current = getattr(self, "_group_report_sort_state", None)
            reverse = bool(current and current[0] == col and not current[1])
        def key_for(iid):
            value = tree.set(iid, col)
            # Empty values shown as '-' should sort as smaller than real numbers.
            if value in {"", "-"}:
                return (0, "")
            try:
                return (1, float(str(value).replace(",", ".").replace("+", "")))
            except Exception:
                return (1, str(value).lower())
        items = list(tree.get_children(""))
        items.sort(key=key_for, reverse=bool(reverse))
        for index, iid in enumerate(items):
            tree.move(iid, "", index)
        self._group_report_sort_state = (col, bool(reverse))
        try:
            tree.heading(col, command=lambda c=col, r=not bool(reverse): self._sort_group_report_tree(c, r))
        except Exception:
            pass

    def _selected_group_report_rows(self):
        tree = getattr(self, "group_report_tree", None)
        selected = []
        if tree is None or not tree.winfo_exists():
            return selected
        row_map = getattr(self, "_group_report_row_map", {}) or {}
        try:
            for iid in tree.selection():
                row = row_map.get(str(iid))
                if row is not None:
                    selected.append(row)
        except Exception:
            pass
        return selected

    def _remove_selected_from_group_report(self):
        selected = self._selected_group_report_rows()
        if not selected:
            messagebox.showinfo(
                self._group_text("Grupperapport", "Group report"),
                self._group_text("Vælg mindst én spiller, der skal fjernes fra rapporten.", "Select at least one player to remove from the report."),
                parent=getattr(self, "group_report_window", self),
            )
            return
        selected_keys_by_group = {}
        for row in selected:
            gid = row.get("group_id") or row.get("group")
            key = row.get("key") or row.get("player")
            if gid and key:
                selected_keys_by_group.setdefault(gid, set()).add(key)
        new_history = []
        for entry in getattr(self, "group_report_history", []) or []:
            gid = entry.get("id") or entry.get("name")
            remove_keys = selected_keys_by_group.get(gid, set())
            if not remove_keys:
                new_history.append(entry)
                continue
            entry = copy.deepcopy(entry)
            entry["players"] = [p for p in (entry.get("players", []) or []) if self._group_player_key(p) not in remove_keys]
            entry["rows"] = [r for r in (entry.get("rows", []) or []) if (r.get("key") or str(r.get("url", "")) or r.get("name", "")) not in remove_keys]
            if entry.get("players") or entry.get("rows"):
                new_history.append(entry)
        self.group_report_history = new_history
        self._refresh_group_report_window()

    def _remove_selected_group_from_report(self):
        selected = self._selected_group_report_rows()
        if not selected:
            messagebox.showinfo(
                self._group_text("Grupperapport", "Group report"),
                self._group_text("Vælg en række i den gruppe, der skal fjernes fra rapporten.", "Select a row in the group to remove from the report."),
                parent=getattr(self, "group_report_window", self),
            )
            return
        group_ids = {row.get("group_id") or row.get("group") for row in selected if (row.get("group_id") or row.get("group"))}
        if not group_ids:
            return
        active_id = (getattr(self, "active_group", None) or {}).get("id")
        active_removed = bool(active_id and active_id in group_ids)
        self.group_report_history = [
            entry for entry in (getattr(self, "group_report_history", []) or [])
            if (entry.get("id") or entry.get("name")) not in group_ids
        ]
        self._refresh_group_report_window()
        if active_removed:
            # 3.60: Fjern gruppe i Grupperapport svarer til Ryd i spillersektionen,
            # når gruppen er aktiv i Kontrolcenter.
            self._reset_main_workspace()

    def _build_group_report_lines(self):
        rows = self._group_report_rows()
        if not rows:
            raise ValueError(self._group_text("Der er ingen grupperapport at eksportere.", "There is no group report to export."))

        lines = []
        lines.append(self._group_text("VMAN Training Planner - grupperapport", "VMAN Training Planner - group report"))
        lines.append("=" * 32)
        lines.append("")
        lines.append(self._group_text(
            f"Eksporteret: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Exported: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        ))
        lines.append(self._group_text(
            f"Spillere i rapporten: {len(rows)}",
            f"Players in report: {len(rows)}",
        ))

        entries = list(getattr(self, "group_report_history", []) or [])
        if entries:
            lines.append("")
            lines.append(self._group_text("Parallelle gruppeforløb", "Parallel group paths"))
            lines.append("-" * 24)
            for entry in entries:
                name = str(entry.get("name", "Gruppe") or "Gruppe")
                count = len(entry.get("players", []) or [])
                source = str(entry.get("program_source", "none") or "none")
                days = entry.get("program_days", 0)
                try:
                    days = int(days)
                except Exception:
                    days = 0
                lines.append(self._group_text(
                    f"{name}: {count} spiller(e), {days} programdage, kilde: {source}",
                    f"{name}: {count} player(s), {days} program days, source: {source}",
                ))

        errors = list(getattr(self, "_group_report_last_errors", []) or [])
        if errors:
            lines.append("")
            lines.append(self._group_text("Advarsler", "Warnings"))
            lines.append("-" * 9)
            for error in errors:
                lines.extend(self._wrap_report_text(f"- {error}", width=92, subsequent_indent="  "))

        lines.append("")
        lines.append(self._group_text("Rapport", "Report"))
        lines.append("-" * 7)
        for row in rows:
            group_name = str(row.get("group", "") or "-")
            player_name = str(row.get("player", "") or "-")
            position = str(row.get("position", "") or "-")
            delta = row.get("delta")
            delta_text = "-" if delta is None else f"{float(delta):+.2f}"
            line1 = self._group_text(
                f"Gruppe: {group_name} | Spiller: {player_name}",
                f"Group: {group_name} | Player: {player_name}",
            )
            line2 = self._group_text(
                "Position: {pos} | Startalder: {age} | Slutalder: {end_age} | Start-VT: {start} | Slut-VT: {final} | Delta-VT: {delta} | Gennemsnit: {avg}",
                "Position: {pos} | Start age: {age} | End age: {end_age} | Start rating: {start} | Final rating: {final} | Delta rating: {delta} | Average: {avg}",
            ).format(
                pos=position,
                age=self._group_report_format_num(row.get("start_age"), 2),
                end_age=self._group_report_format_num(row.get("final_age"), 2),
                start=self._group_report_format_num(row.get("start_rating"), 2),
                final=self._group_report_format_num(row.get("final_rating"), 2),
                delta=delta_text,
                avg=self._group_report_format_num(row.get("average"), 2),
            )
            lines.extend(self._wrap_report_text(line1, width=92, subsequent_indent="  "))
            lines.extend(self._wrap_report_text(line2, width=92, subsequent_indent="  "))
            lines.append("")

        return lines

    def _export_group_report_pdf(self):
        try:
            lines = self._build_group_report_lines()
            path = filedialog.asksaveasfilename(
                defaultextension=".pdf",
                filetypes=[("PDF", "*.pdf")],
                title=self._group_text("Eksportér grupperapport som PDF", "Export group report as PDF"),
                parent=getattr(self, "group_report_window", self),
            )
            if not path:
                return
            self._write_simple_pdf(path, lines)
            if hasattr(self, "group_report_status_var"):
                self.group_report_status_var.set(self._group_text(f"PDF gemt: {path}", f"PDF saved: {path}"))
        except Exception as error:
            messagebox.showerror(
                self._group_text("Grupperapport", "Group report"),
                str(error),
                parent=getattr(self, "group_report_window", self),
            )

    def _export_group_report_doc(self):
        try:
            lines = self._build_group_report_lines()
            path = filedialog.asksaveasfilename(
                defaultextension=".doc",
                filetypes=[("Word-kompatibel DOC", "*.doc"), ("RTF", "*.rtf")],
                title=self._group_text("Eksportér grupperapport som DOC", "Export group report as DOC"),
                parent=getattr(self, "group_report_window", self),
            )
            if not path:
                return

            section_titles = {
                self._group_text("VMAN Training Planner - grupperapport", "VMAN Training Planner - group report"),
                self._group_text("Parallelle gruppeforløb", "Parallel group paths"),
                self._group_text("Advarsler", "Warnings"),
                self._group_text("Rapport", "Report"),
            }
            rtf_lines = [
                r"{\rtf1\ansi\deff0",
                r"{\fonttbl{\f0 Helvetica;}}",
                r"\fs22",
            ]
            for line in lines:
                if line in section_titles:
                    rtf_lines.append(r"\par\b " + self._rtf_escape(line) + r"\b0\par")
                elif set(str(line)) <= {"=", "-"} and line:
                    continue
                elif line == "":
                    rtf_lines.append(r"\par")
                else:
                    rtf_lines.append(self._rtf_escape(line) + r"\par")
            rtf_lines.append("}")

            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(rtf_lines))
            if hasattr(self, "group_report_status_var"):
                self.group_report_status_var.set(self._group_text(f"DOC gemt: {path}", f"DOC saved: {path}"))
        except Exception as error:
            messagebox.showerror(
                self._group_text("Grupperapport", "Group report"),
                str(error),
                parent=getattr(self, "group_report_window", self),
            )

    def _save_group_report_debug(self):
        try:
            initialfile = f"vman_group_report_debug_{time.strftime('%Y%m%d_%H%M%S')}.txt"
            path = filedialog.asksaveasfilename(
                title=self._group_text("Gem grupperapport-debug", "Save group report debug"),
                initialfile=initialfile,
                defaultextension=".txt",
                filetypes=[("Text", "*.txt"), ("All files", "*.*")],
                parent=getattr(self, "group_report_window", self),
            )
            if not path:
                return
            active = getattr(self, "active_group", None) or {}
            rows = self._group_report_rows()
            errors = list(getattr(self, "_group_report_last_errors", []) or [])
            lines = []
            lines.append("VMAN Training Planner grupperapport-debug")
            lines.append("================================")
            lines.append(f"Tid: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append(f"Aktiv gruppe: {active.get('name', '-')}")
            lines.append(f"Aktiv gruppe-id: {active.get('id', '-')}")
            report_program, report_program_source = self._group_report_program()
            try:
                report_program_days = sum(int(getattr(item, "days", 0) or 0) for item in expand_program(report_program or []))
            except Exception:
                report_program_days = sum(int(getattr(item, "days", 0) or 0) for item in report_program or [])
            lines.append(f"Programdage: {report_program_days}")
            lines.append(f"Programkilde: {report_program_source}")
            try:
                lines.append(f"Assistent-resultater: {len(self._assistant_all_results())}")
            except Exception:
                lines.append("Assistent-resultater: -")
            try:
                lines.append(f"Hovedposition: {internal_position(self.position_var.get())}")
            except Exception:
                lines.append(f"Hovedposition: {self.position_var.get()}")
            lines.append(f"XP-felt: {self.xp_var.get() if hasattr(self, 'xp_var') else '-'}")
            lines.append("")
            lines.append("Rapport-rækker:")
            for row in rows:
                lines.append(json.dumps(row, ensure_ascii=False, default=str))
            lines.append("")
            lines.append("Fejl / advarsler:")
            if errors:
                for error in errors:
                    lines.append(f"- {error}")
            else:
                lines.append("- Ingen registrerede fejl.")
            lines.append("")
            lines.append("Rapport-historik rådata:")
            lines.append(json.dumps(getattr(self, "group_report_history", []), ensure_ascii=False, indent=2, default=str))
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            if hasattr(self, "group_report_status_var"):
                self.group_report_status_var.set(self._group_text(f"Debug gemt: {path}", f"Debug saved: {path}"))
        except Exception as error:
            messagebox.showerror(
                self._group_text("Grupperapport", "Group report"),
                str(error),
                parent=getattr(self, "group_report_window", self),
            )

    def _save_group_avatar_debug(self):
        try:
            initialfile = f"vman_group_avatar_debug_{time.strftime('%Y%m%d_%H%M%S')}.txt"
            path = filedialog.asksaveasfilename(
                title=self._group_text("Gem avatar-debug", "Save avatar debug"),
                initialfile=initialfile,
                defaultextension=".txt",
                filetypes=[("Text", "*.txt"), ("All files", "*.*")],
                parent=getattr(self, "group_report_window", self),
            )
            if not path:
                return
            active = getattr(self, "active_group", None) or {}
            lines = []
            lines.append("VMAN Training Planner gruppe-avatar-debug")
            lines.append("=================================")
            lines.append(f"Tid: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append(f"Aktiv gruppe: {active.get('name', '-')}")
            lines.append(f"Aktiv gruppe-id: {active.get('id', '-')}")
            lines.append(f"Spillere i aktiv gruppe: {len(active.get('players', []) or [])}")
            cache = getattr(self, "_group_avatar_photo_cache", {}) or {}
            order = list(getattr(self, "_group_avatar_rotation_keys", []) or [])
            lines.append(f"Avatarer i cache: {len(cache)}")
            lines.append(f"Rotationsrækkefølge: {order}")
            lines.append(f"Aktuel rotationsnøgle: {getattr(self, '_group_avatar_current_key', None)}")
            lines.append(f"Nuværende rotationsindeks: {getattr(self, '_group_avatar_rotation_index', None)}")
            lines.append(f"Efter-plan aktiv: {getattr(self, '_group_avatar_rotation_after_id', None) is not None}")
            lines.append("")
            lines.append("Aktive spillere:")
            for player in active.get("players", []) or []:
                lines.append(json.dumps({
                    "key": self._group_player_key(player),
                    "name": player.get("name"),
                    "url": player.get("url"),
                    "avatar_url": player.get("avatar_url"),
                    "avatar_image_bytes": len(player.get("avatar_image_bytes") or b""),
                    "avatar_content_type": player.get("avatar_content_type"),
                }, ensure_ascii=False, default=str))
            lines.append("")
            lines.append("Henteforsøg:")
            for rec in getattr(self, "_group_avatar_debug_records", []) or []:
                lines.append(json.dumps(rec, ensure_ascii=False, default=str))
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            if hasattr(self, "group_report_status_var"):
                self.group_report_status_var.set(self._group_text(f"Avatar-debug gemt: {path}", f"Avatar debug saved: {path}"))
        except Exception as error:
            messagebox.showerror(
                self._group_text("Grupperapport", "Group report"),
                str(error),
                parent=getattr(self, "group_report_window", self),
            )

    def _refresh_group_report_window(self):
        tree = getattr(self, "group_report_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        tree.delete(*tree.get_children())
        rows = self._group_report_rows()
        self._group_report_row_map = {}
        for idx, row in enumerate(rows):
            self._group_report_row_map[str(idx)] = row
            delta = row.get("delta")
            delta_text = "-" if delta is None else f"{float(delta):+.2f}"
            tree.insert("", "end", iid=str(idx), values=(
                row.get("group", ""),
                row.get("player", ""),
                row.get("position", ""),
                self._group_report_format_num(row.get("start_age"), 2),
                self._group_report_format_num(row.get("final_age"), 2),
                self._group_report_format_num(row.get("start_rating"), 2),
                self._group_report_format_num(row.get("final_rating"), 2),
                delta_text,
                self._group_report_format_num(row.get("average"), 2),
            ))
        if hasattr(self, "group_report_status_var"):
            errors = list(getattr(self, "_group_report_last_errors", []) or [])
            if rows:
                if errors:
                    self.group_report_status_var.set(
                        self._group_text(
                            f"{len(rows)} spillere i rapporten. {len(errors)} kunne ikke beregnes — prøv igen efter kontrol af data.",
                            f"{len(rows)} players in the report. {len(errors)} could not be calculated — check the data and try again.",
                        )
                    )
                else:
                    entries = list(getattr(self, "group_report_history", []) or [])
                    missing_program = [
                        str(entry.get("name", "Group"))
                        for entry in entries
                        if not entry.get("program_snapshot")
                    ]
                    sources = sorted(set(
                        str(entry.get("program_source", "none"))
                        for entry in entries
                        if entry.get("program_snapshot")
                    ))
                    if missing_program:
                        self.group_report_status_var.set(
                            self._group_text(
                                f"{len(rows)} spillere i rapporten. Mangler program for: {', '.join(missing_program)}.",
                                f"{len(rows)} players in the report. Missing program for: {', '.join(missing_program)}.",
                            )
                        )
                    else:
                        source_text = ", ".join(sources) if sources else "gruppeprogrammer"
                        self.group_report_status_var.set(
                            self._group_text(
                                f"{len(rows)} spillere i rapporten. Parallelle gruppeforløb: {source_text}.",
                                f"{len(rows)} players in the report. Parallel group paths: {source_text}.",
                            )
                        )
            else:
                self.group_report_status_var.set(self._group_text("Ingen grupper endnu.", "No groups yet."))
        sort_state = getattr(self, "_group_report_sort_state", None)
        if sort_state:
            self._sort_group_report_tree(sort_state[0], sort_state[1])

    def _fetch_player_link_to_assistant(self):
        url = str(self.assistant_player_link_var.get()).strip() if hasattr(self, "assistant_player_link_var") else ""
        if not url:
            if hasattr(self, "assistant_player_link_status_var"):
                self.assistant_player_link_status_var.set("Ugyldigt link")
            return
        try:
            if hasattr(self, "assistant_player_link_status_var"):
                self.assistant_player_link_status_var.set(self._tr("Henter spiller…"))
                self.update_idletasks()
            snap = fetch_player_snapshot(url)
            old_stats = self.assistant_data.get("start_stats", {})
            new_position = snap.engine_position
            self.assistant_data["player_link"] = url
            self.assistant_data["position"] = new_position
            self.assistant_data["weights"] = dict(POSITION_WEIGHTS[new_position])
            imported_stats = getattr(snap, "stats", None) or {}
            self.assistant_data["start_stats"] = {
                stat: float(imported_stats.get(stat, old_stats.get(stat, 2.0)))
                for stat in POSITION_STATS[new_position]
            }
            if snap.alder is not None:
                self.assistant_data["start_age"] = snap.alder
                if float(self.assistant_data.get("end_age", 26.0)) < snap.alder:
                    self.assistant_data["end_age"] = snap.alder
            imported_xp = self._format_imported_training_xp(getattr(snap, "training_xp_average", None))
            if imported_xp is not None:
                self.assistant_data["xp"] = imported_xp
            self.assistant_data["_player_import_status"] = self._player_link_summary_text(snap)
            self._render_assistant_step()
        except Exception as error:
            if hasattr(self, "assistant_player_link_status_var"):
                self.assistant_player_link_status_var.set("Ugyldigt link")

    def _debug_player_link_html(self, assistant=False):
        if assistant:
            url = str(self.assistant_player_link_var.get()).strip() if hasattr(self, "assistant_player_link_var") else ""
            parent = self.assistant_window if self.assistant_window is not None else self
            status_var = getattr(self, "assistant_player_link_status_var", None)
        else:
            # Brug altid det rå link/id. Feltet kan vise spillernavnet efter import.
            url = self._current_player_link_reference() if hasattr(self, "player_link_var") else ""
            parent = self
            status_var = getattr(self, "player_link_status_var", None)

        if not url:
            messagebox.showerror("Player link debug", self._tr("Indsæt først et spillerlink."), parent=parent)
            return

        try:
            if status_var is not None:
                status_var.set(self._tr("Gemmer debug-filer…"))
                self.update_idletasks()
            out_dir = build_player_debug_bundle(url)
            if status_var is not None:
                status_var.set(f"Debug gemt: {out_dir}")
            messagebox.showinfo(
                "Spillerlink-debug",
                "Debug-filer er gemt på din Mac.\n\n"
                f"Mappe:\n{out_dir}\n\n"
                "Zip mappen og upload den her i chatten.",
                parent=parent,
            )
        except Exception as error:
            if status_var is not None:
                status_var.set("Kunne ikke gemme debug.")
            messagebox.showerror("Spillerlink-debug", str(error), parent=parent)


    def _build_ui(self):
        # 3.78: Redesign af Kontrolcenteret. Der bruges en fast topbjælke
        # til app/menu og et todelt hovedlayout nedenunder. Funktionerne bygges
        # fortsat af de eksisterende moduler, så simulator/import/Assistent-logik
        # ikke ændres.
        root = ttk.Frame(self, padding=(14, 10, 14, 14))
        self.root_frame = root
        root.pack(fill="both", expand=True)

        self.header_bar = ttk.Frame(root)
        self.header_bar.place(relx=1.0, x=-14, y=0, anchor="ne")

        # 3.79: Ingen tekst i topbjælken. Den bruges kun til hamburgeren.
        self.content_frame = ttk.Frame(root)
        self.content_frame.pack(fill="both", expand=True, pady=(15, 0))

        self.left_panel = ttk.Frame(self.content_frame, width=381)
        self.left_panel.pack(side="left", fill="both", padx=(0, 12))
        self.left_panel.pack_propagate(False)

        self.right_panel = ttk.Frame(self.content_frame)
        self.right_panel.pack(side="right", fill="both", expand=True)

        self._build_settings(self.left_panel)
        # 2.24: Egenskaber/startstats er nu en integreret del af Spiller-panelet.
        # Derfor bygges de inde i settings_frame i stedet for som en separat
        # venstrepanel-sektion med luft imellem.
        self._build_start_stats(self.settings_frame)
        self._build_program_builder(self.left_panel)
        self._build_program_list(self.right_panel)
        self._build_main_section_title_overlays()

        self._right_section_sync_job = None
        self.after_idle(self._sync_right_sections_with_left)

        for widget in (
            self.left_panel,
            self.right_panel,
            self.content_frame,
            self.start_stats_frame,
            self.program_builder_frame,
            self.program_list_frame,
            self.result_section_frame,
        ):
            try:
                widget.bind("<Configure>", self._schedule_right_section_sync, add="+")
            except Exception:
                pass


    def _build_main_section_title_overlays(self):
        """Draw main section titles as real overlay labels above the frame line.

        Ttk LabelFrame titles cannot be moved reliably on Windows. These labels
        live in the same parent as the sections and are positioned just above the
        border, so the title sits over the line instead of being trapped below it.
        """
        self.main_section_title_labels = []
        for frame_name, key in (
            ("settings_frame", "Spiller"),
            ("program_list_frame", "Træningsprogram"),
            ("program_builder_frame", "Session"),
            ("result_section_frame", "Resultat"),
        ):
            frame = getattr(self, frame_name, None)
            if frame is None:
                continue
            try:
                parent = getattr(self, "root_frame", frame.nametowidget(frame.winfo_parent()))
                bg = ttk.Style(self).lookup("TFrame", "background") or "#2f2b2b"
                label = tk.Label(
                    parent,
                    text=self._tr(key),
                    font=("", 14, "bold"),
                    bg=bg,
                    fg=getattr(self, "_theme_panel_fg", "#f0f0f0"),
                    bd=0,
                    padx=2,
                    pady=0,
                )
                setattr(label, "_vman_i18n_key", key)
                self.main_section_title_labels.append((frame, label))
                label.lift()
                frame.bind("<Configure>", self._position_main_section_title_overlays, add="+")
                parent.bind("<Configure>", self._position_main_section_title_overlays, add="+")
            except Exception:
                pass
        self.after_idle(self._position_main_section_title_overlays)

    def _position_main_section_title_overlays(self, event=None):
        try:
            for frame, label in getattr(self, "main_section_title_labels", []):
                if not frame.winfo_exists() or not label.winfo_exists():
                    continue
                parent = getattr(self, "root_frame", frame.nametowidget(frame.winfo_parent()))
                root_x = parent.winfo_rootx()
                root_y = parent.winfo_rooty()
                x = frame.winfo_rootx() - root_x - 12
                # 3.81: Titlen ligger helt over rammelinjen, så labelens
                # baggrund ikke laver et hul i sektionens afgrænsning.
                y = frame.winfo_rooty() - root_y - max(18, label.winfo_reqheight() - 1)
                y = max(0, y)
                placement = (str(parent), int(x), int(y))
                if getattr(label, "_vman_cached_overlay_place", None) != placement:
                    label.place(in_=parent, x=x, y=y, anchor="nw")
                    label._vman_cached_overlay_place = placement
                label.lift()
        except Exception:
            pass

    def _limit_decimal_text(self, text, decimals=2, max_integer_digits=None):
        raw = str(text or "").replace(",", ".")
        out = []
        dot_seen = False

        for ch in raw:
            if ch.isdigit():
                out.append(ch)
            elif ch == "." and not dot_seen:
                out.append(".")
                dot_seen = True

        cleaned = "".join(out)
        if cleaned.startswith("."):
            cleaned = "0" + cleaned

        if "." in cleaned:
            whole, frac = cleaned.split(".", 1)
            if max_integer_digits is not None:
                whole = whole[:max_integer_digits]
            if decimals <= 0:
                cleaned = whole
            else:
                cleaned = whole + "." + frac[:decimals]
        else:
            if max_integer_digits is not None:
                cleaned = cleaned[:max_integer_digits]

        return cleaned

    def _schedule_settings_changed(self, change_scope="player"):
        try:
            scope = str(change_scope or "player").strip().lower()
            pending = str(getattr(self, "_settings_changed_pending_scope", "") or "").lower()
            # Player is the stronger scope if multiple edits coalesce.
            if pending != "player":
                self._settings_changed_pending_scope = scope
            if getattr(self, "_settings_changed_scheduled", False):
                return
            self._settings_changed_scheduled = True
            self.after_idle(self._run_scheduled_settings_changed)
        except Exception:
            try:
                self._settings_changed(change_scope)
            except Exception:
                pass

    def _run_scheduled_settings_changed(self):
        self._settings_changed_scheduled = False
        scope = getattr(self, "_settings_changed_pending_scope", "player") or "player"
        self._settings_changed_pending_scope = ""
        self._settings_changed(scope)

    def _on_xp_changed(self, *_):
        if getattr(self, "_xp_normalizing", False):
            return

        raw = self.xp_var.get()
        limited = self._limit_decimal_text(raw, decimals=0, max_integer_digits=3)

        if limited != raw:
            self._xp_normalizing = True
            try:
                self.xp_var.set(limited)
            finally:
                self._xp_normalizing = False

        self._settings_changed()


    def _apply_session_interval_baseline_width(self):
        """Hold Session-modulets bredde fast efter Intervaltræning.

        Nogle øvelser har længere/kortere egenskabsnavne, hvilket ellers får
        Pointfordeling-modulet og knapperne i Session-panelet til at ændre
        horisontal placering. Referencebredden måles fra Intervaltræning og
        genbruges derefter for alle øvelser.
        """
        width = getattr(self, "_session_interval_baseline_width", None)
        if not width:
            return

        try:
            width = int(width)
            if hasattr(self, "session_width_spacer"):
                self.session_width_spacer.configure(width=width)
                self.session_width_spacer.grid_propagate(False)
            # Pointfordeling-panelet må ikke låses med grid_propagate(False),
            # for så bliver højden også fast og kan klippe øvelser med flere
            # egenskaber. Den faste bredde opstår i stedet via labelkolonnen og
            # den usynlige spacer.
        except Exception:
            pass


    def _apply_session_button_vertical_offset(self):
        """Use Teknik/markspiller button placement for all exercises except Træningskamp."""
        try:
            exercise = internal_exercise(self.exercise_var.get()) if hasattr(self, "exercise_var") else ""
            if exercise == "Træningskamp":
                if hasattr(self, "add_button"):
                    self.add_button.grid_configure(pady=(6, 0))
                if hasattr(self, "update_button"):
                    self.update_button.grid_configure(pady=(9, 0))
            else:
                # 3.80: Almindelige øvelser skal have knapperne trukket op,
                # så Opdater ikke bliver spist i bunden af Session-panelet.
                if hasattr(self, "add_button"):
                    self.add_button.grid_configure(pady=(0, 0))
                if hasattr(self, "update_button"):
                    self.update_button.grid_configure(pady=(1, 0))
        except Exception:
            pass

    def _lock_session_buttons_to_technique_baseline(self):
        """Hold non-Træningskamp buttons at Teknik/markspiller Y-position."""
        try:
            exercise = internal_exercise(self.exercise_var.get()) if hasattr(self, "exercise_var") else ""
            if exercise == "Træningskamp":
                if hasattr(self, "dist_frame"):
                    try:
                        self.dist_frame.grid_propagate(True)
                    except Exception:
                        pass
                    try:
                        self.dist_frame.configure(height=1)
                    except Exception:
                        pass
                return

            row_height = int(getattr(self, "_session_technique_distribution_row_height", 0) or 0)
            if row_height <= 0:
                return

            self.program_builder_frame.rowconfigure(2, minsize=row_height)
            if hasattr(self, "dist_frame"):
                try:
                    self.dist_frame.configure(height=row_height)
                    self.dist_frame.grid_propagate(False)
                except Exception:
                    pass
        except Exception:
            pass

    def _apply_session_technique_layout_baseline(self):
        """Hold Session-panelets ikke-Træningskamp-layout fast efter Teknik.

        For almindelige øvelser skal knapperne "Tilføj til træningsprogram"
        og "Opdater" blive stående samme sted som ved Teknik for
        markspillere. Træningskamp er undtaget og beholder sit kompakte
        layout med knapperne oppe.
        """
        if getattr(self, "_session_baseline_calibrating", False):
            return
        if not hasattr(self, "program_builder_frame"):
            return

        exercise = internal_exercise(self.exercise_var.get()) if hasattr(self, "exercise_var") else ""
        self._apply_session_button_vertical_offset()

        try:
            if exercise == "Træningskamp":
                self.program_builder_frame.rowconfigure(2, minsize=0)
                if hasattr(self, "dist_frame"):
                    try:
                        self.dist_frame.grid_propagate(True)
                    except Exception:
                        pass
                return

            # 3.86: Alle ikke-Træningskamp-øvelser låses til samme
            # knapplacering som Teknik/Forsvar.
            self._lock_session_buttons_to_technique_baseline()

            # 3.73: Selve Session-panelets højde styres af den fælles
            # Kontrolcenter-layoutsynkronisering, så bunden flugter med
            # Resultat. Denne funktion må derfor ikke længere nulstille
            # panelets faste højde.
        except Exception:
            pass


    def _calibrate_session_interval_baseline_width(self):
        """Mål Session-modulets referencebredde ved Intervaltræning."""
        if getattr(self, "_session_width_calibrating", False):
            return
        if not all(hasattr(self, name) for name in ("position_var", "exercise_var", "dist_frame")):
            return

        self._session_width_calibrating = True
        old_position = internal_position(self.position_var.get())
        old_exercise = self.exercise_var.get()
        try:
            # Intervaltræning findes for både markspillere og keepere, men
            # markspiller/Forsvar giver den ønskede reference for hovedlayoutet.
            self.position_var.set("Forsvar")
            self.exercise_var.set("Intervaltræning")
            self._refresh_distribution_inputs()
            self.update_idletasks()
            measured = max(
                int(self.dist_frame.winfo_width() or 0),
                int(self.dist_frame.winfo_reqwidth() or 0),
                int(self.add_button.winfo_width() or 0) if hasattr(self, "add_button") else 0,
            )
            if measured > 0:
                self._session_interval_baseline_width = measured
        except Exception:
            self._session_interval_baseline_width = None
        finally:
            try:
                self.position_var.set(old_position)
                self.exercise_var.set(old_exercise)
                self._refresh_distribution_inputs()
                self._apply_session_interval_baseline_width()
                self.update_idletasks()
            except Exception:
                pass
            self._session_width_calibrating = False

    def _calibrate_session_technique_baseline(self):
        """Mål Session-panelets referencehøjde ved Teknik for markspillere.

        2.31: Session-panelets egen højde er rullet tilbage til naturlig
        højde. Resultat-panelets bund bruger fortsat den faste reference:
        sådan som Session-panelet ser ud ved øvelsen "Teknik" for en
        markspiller.
        """
        if getattr(self, "_session_baseline_calibrating", False):
            return
        if not all(hasattr(self, name) for name in ("position_var", "exercise_var", "program_builder_frame")):
            return

        self._session_baseline_calibrating = True
        old_position = internal_position(self.position_var.get())
        old_exercise = self.exercise_var.get()
        try:
            self.position_var.set("Forsvar")
            self.exercise_var.set("Teknik")
            self._refresh_distribution_inputs()
            self.update_idletasks()
            measured = int(self.program_builder_frame.winfo_height())
            if measured > 0:
                self._session_technique_baseline_height = measured

            try:
                # Højden på Pointfordeling-rækken afgør, hvor knapperne
                # nedenunder starter. Den måles ved Teknik og genbruges for
                # alle almindelige øvelser, så knapperne ikke hopper vertikalt.
                row_bbox = self.program_builder_frame.grid_bbox(0, 2, 3, 2)
                row_height = int(row_bbox[3]) if row_bbox else 0
                if row_height > 0:
                    self._session_technique_distribution_row_height = row_height
            except Exception:
                self._session_technique_distribution_row_height = None
        except Exception:
            self._session_technique_baseline_height = None
        finally:
            try:
                self.position_var.set(old_position)
                self.exercise_var.set(old_exercise)
                self._refresh_distribution_inputs()
                self.update_idletasks()
            except Exception:
                pass
            self._session_baseline_calibrating = False
            self._apply_session_technique_layout_baseline()
            self._schedule_right_section_sync()

    def _schedule_right_section_sync(self, event=None):
        if not getattr(self, "_layout_sync_enabled", True):
            return
        if getattr(self, "_syncing_right_sections", False):
            return
        # 4.51: Behold én pending sync i stedet for at cancel/reschedule ved
        # hvert Configure/trace-event. Det reducerer UI-churn markant.
        if getattr(self, "_right_section_sync_job", None):
            return
        self._right_section_sync_job = self.after(90, self._sync_right_sections_with_left)

    def _sync_right_sections_with_left(self):
        self._right_section_sync_job = None
        if getattr(self, "_syncing_right_sections", False):
            return
        required = [
            "left_panel",
            "right_panel",
            "settings_frame",
            "program_builder_frame",
            "program_list_frame",
            "result_section_frame",
        ]
        if not all(hasattr(self, name) for name in required):
            return

        self._syncing_right_sections = True
        changed = False
        try:
            # 4.51: Ingen unconditional update_idletasks her. Den var den klart
            # dyreste kilde til lag. Vi bruger aktuelle/ønskede mål og cacher.
            panel_h = min(
                int(self.left_panel.winfo_height() or 0),
                int(self.right_panel.winfo_height() or 0),
            )
            if panel_h <= 0:
                return

            row_gap = 33
            available = max(520, panel_h - row_gap)

            settings_req = int(self.settings_frame.winfo_reqheight() or 0)
            program_req = int(self.program_list_frame.winfo_reqheight() or 0)
            builder_req = int(self.program_builder_frame.winfo_reqheight() or 0)
            result_req = int(self.result_section_frame.winfo_reqheight() or 0)

            top_reference = max(settings_req, program_req, 315)
            min_top = max(410, int(top_reference * 0.97))
            # 4.66 Windows: Den hvide resultatflade gøres højere, men den må
            # ikke tvinge selve Session/Resultat-sektionernes bundlinjer længere
            # ned. Derfor bruges result_req kun som minimum på ikke-Windows.
            result_req_for_min = 0 if self._is_windows_ui() else result_req
            min_bottom = max(
                int(getattr(self, "_session_technique_baseline_height", 0) or 0),
                builder_req,
                result_req_for_min,
                320 if self._is_windows_ui() else 245,
            )

            top_h = max(0, min_top - 10)
            # 4.65 Windows: udnyt den ubrugte plads nederst i Kontrolcenteret.
            # Session- og Resultat-panelets bundgrænser må nu gå ca. 25 px længere ned,
            # uden at Mac-layoutet påvirkes.
            preferred_bottom = 407 if self._is_windows_ui() else 357
            bottom_h = max(min_bottom, preferred_bottom)
            if top_h + bottom_h > available:
                bottom_h = max(min_bottom, available - top_h)
            max_bottom = 432 if self._is_windows_ui() else 377
            if bottom_h > max_bottom:
                bottom_h = max_bottom

            geometry_key = (panel_h, settings_req, program_req, builder_req, result_req, top_h, bottom_h)
            if getattr(self, "_right_section_geometry_cache", None) == geometry_key:
                return
            self._right_section_geometry_cache = geometry_key

            for widget, height in (
                (self.settings_frame, top_h),
                (self.program_list_frame, top_h),
                (self.program_builder_frame, bottom_h),
                (self.result_section_frame, bottom_h),
            ):
                try:
                    if not getattr(widget, "_vman_propagation_locked", False):
                        widget.pack_propagate(False)
                        try:
                            widget.grid_propagate(False)
                        except Exception:
                            pass
                        widget._vman_propagation_locked = True
                    if int(widget.winfo_height() or 0) != int(height):
                        widget.configure(height=int(height))
                        changed = True
                except Exception:
                    pass
        except Exception:
            return
        finally:
            self._syncing_right_sections = False
            if changed:
                try:
                    self._position_main_section_title_overlays()
                except Exception:
                    pass

    def _build_settings(self, parent):
        frame = ttk.LabelFrame(parent, text="", padding=10, style="MainSection.TLabelframe")
        self.settings_frame = frame
        frame.pack(fill="x", expand=False, pady=(0, 33))
        frame.columnconfigure(0, weight=1)

        row_gap = (2, 5)
        label_padx = getattr(self, "_shared_label_padx", (0, 0))

        # 2.39: Spillerdata og avatar placeres i et fast top-layout. Avatarens
        # kolonne er ikke længere en del af samme grid som startstats, så den
        # kan ikke skubbe felter og egenskaber skævt ud af deres kolonner.
        player_top_frame = ttk.Frame(frame)
        self.player_top_frame = player_top_frame
        player_top_frame.grid(row=0, column=0, sticky="ew")
        player_top_frame.columnconfigure(0, weight=0)
        player_top_frame.columnconfigure(1, weight=0)
        player_top_frame.columnconfigure(2, weight=1)

        player_fields_frame = ttk.Frame(player_top_frame)
        self.player_fields_frame = player_fields_frame
        player_fields_frame.grid(row=0, column=0, sticky="new", padx=(0, 10))
        player_fields_frame.columnconfigure(0, weight=0)
        player_fields_frame.columnconfigure(1, weight=1)

        # 2.41: Ny portrætramme efter VMAN-billedets naturlige dimensioner
        # 180×220. Den er fast og rektangulær allerede før import, uden tekst.
        avatar_border = int(getattr(self, "_player_avatar_border", 1))
        avatar_box_width = int(getattr(self, "_player_avatar_box_width", 92))
        avatar_box_height = int(getattr(self, "_player_avatar_box_height", 112))
        self.player_avatar_canvas = tk.Canvas(
            player_top_frame,
            width=avatar_box_width,
            height=avatar_box_height,
            background=(getattr(self, "_theme_panel_bg", "#2f2b2b")),
            borderwidth=0,
            highlightthickness=0,
        )
        self.player_avatar_canvas.grid(row=0, column=1, sticky="nw", padx=(22, 0), pady=(0, 2))
        self.player_avatar_box = self.player_avatar_canvas
        self.player_avatar_label = self.player_avatar_canvas
        self._set_player_avatar_placeholder()

        ttk.Label(player_fields_frame, text="Spillerlink").grid(row=0, column=0, sticky="w", padx=label_padx, pady=row_gap)
        self.player_link_var = tk.StringVar(value="")
        self.player_link_var.trace_add("write", self._on_player_link_var_changed)
        self.player_ref_entry = ttk.Entry(player_fields_frame, textvariable=self.player_link_var, width=20)
        self.player_ref_entry.grid(row=0, column=1, sticky="w", padx=(4, 0), pady=row_gap)
        self.player_ref_entry.bind("<FocusIn>", self._activate_player_link_entry)
        self.player_ref_entry.bind("<Double-Button-1>", self._open_group_import_window)
        self.player_ref_entry.bind("<FocusOut>", lambda event: self._show_player_link_display())
        ttk.Label(player_fields_frame, text="Importer:").grid(row=1, column=0, sticky="w", padx=label_padx, pady=(0, 7))
        player_button_frame = ttk.Frame(player_fields_frame)
        player_button_frame.grid(row=1, column=1, sticky="w", padx=(4, 0), pady=(0, 7))
        self.player_fetch_button = ttk.Button(player_button_frame, text="spiller", width=6, command=self._fetch_player_link_to_main)
        self.player_fetch_button.grid(row=0, column=0, sticky="w", ipadx=2)
        self.group_import_main_button = ttk.Button(player_button_frame, text="gruppe", width=6, command=self._open_group_import_from_player_button)
        self.group_import_main_button.grid(row=0, column=1, sticky="w", padx=(4, 0), ipadx=2)

        ttk.Label(player_fields_frame, text="Position").grid(row=2, column=0, sticky="w", padx=label_padx, pady=row_gap)
        self.position_var = tk.StringVar(value="Keeper")
        self.position_box = self._create_fixed_dropdown(
            player_fields_frame,
            self.position_var,
            values=self._position_options(),
            width_px=98,
        )
        self.position_box.grid(row=2, column=1, sticky="w", padx=(4, 0), pady=row_gap)
        self.position_box.bind("<<ComboboxSelected>>", lambda event: self._on_position_change())

        ttk.Label(player_fields_frame, text="Alder").grid(row=3, column=0, sticky="w", padx=label_padx, pady=row_gap)
        self.start_age_var = tk.StringVar(value="15.0000000000")
        self.start_age_year_var = tk.StringVar(value="15")
        self.start_age_day_var = tk.StringVar(value="0")
        start_age_frame = ttk.Frame(player_fields_frame)
        start_age_frame.grid(row=3, column=1, sticky="w", padx=(4, 0), pady=row_gap)
        self.start_age_year_spinbox = tk.Spinbox(
            start_age_frame,
            from_=15,
            to=40,
            textvariable=self.start_age_year_var,
            width=int(getattr(self, "_main_numeric_field_width", 4)),
            command=self._on_main_start_age_parts_changed,
        )
        self.start_age_year_spinbox.pack(side="left")
        self._configure_numeric_widget(self.start_age_year_spinbox, integer=True, max_value=40)
        ttk.Label(start_age_frame, text="år").pack(side="left", padx=(4, 8))
        self.start_age_day_spinbox = tk.Spinbox(
            start_age_frame,
            from_=0,
            to=29,
            textvariable=self.start_age_day_var,
            width=int(getattr(self, "_main_numeric_field_width", 4)),
            command=self._on_main_start_age_parts_changed,
        )
        self.start_age_day_spinbox.pack(side="left", padx=(3, 0))
        self._configure_numeric_widget(self.start_age_day_spinbox, integer=True, max_value=29)
        ttk.Label(start_age_frame, text="dage").pack(side="left", padx=(4, 0))
        self.start_age_year_var.trace_add("write", self._on_main_start_age_parts_changed)
        self.start_age_day_var.trace_add("write", self._on_main_start_age_parts_changed)
        for spin in (self.start_age_year_spinbox, self.start_age_day_spinbox):
            spin.bind("<FocusOut>", lambda event: self._normalize_main_start_age_parts())
            spin.bind("<Return>", lambda event: self._normalize_main_start_age_parts())

        ttk.Label(player_fields_frame, text="XP").grid(row=4, column=0, sticky="w", padx=label_padx, pady=row_gap)
        self.xp_var = tk.StringVar(value="180")
        xp_row_frame = ttk.Frame(player_fields_frame)
        xp_row_frame.grid(row=4, column=1, sticky="w", padx=(4, 0), pady=row_gap)
        self.xp_entry = tk.Spinbox(
            xp_row_frame,
            from_=0,
            to=999,
            textvariable=self.xp_var,
            width=int(getattr(self, "_main_numeric_field_width", 4)),
            command=self._settings_changed,
        )
        self.xp_entry.pack(side="left")
        self._configure_numeric_widget(self.xp_entry, integer=True, max_value=999)
        self.xp_var.trace_add("write", self._on_xp_changed)

        self.xp_at_label = ttk.Label(xp_row_frame, text="ved", width=3, anchor="center")
        self.xp_at_label.pack(side="left", padx=(3, 4))
        self.xp_reference_intensity_var = tk.StringVar(value=self._display_intensity("Hård"))
        self.xp_reference_intensity_box = self._create_fixed_intensity_dropdown(
            xp_row_frame,
            self.xp_reference_intensity_var,
            self._settings_changed,
        )
        self.xp_reference_intensity_box._vman_lowercase_display = True
        self.xp_reference_intensity_box._vman_lowercase_menu = True
        self.xp_reference_intensity_box.pack(side="left")
        self.xp_intensity_suffix_parent = player_top_frame
        self.xp_intensity_suffix_label = ttk.Label(player_top_frame, text=self._tr("intensitet"))
        self.after_idle(self._sync_intensity_suffix_labels)
        self.after(50, self._sync_intensity_suffix_labels)
        self.after(150, self._sync_intensity_suffix_labels)
        player_top_frame.bind("<Configure>", self._sync_intensity_suffix_labels, add="+")
        self.xp_reference_intensity_box.bind("<Configure>", self._sync_intensity_suffix_labels, add="+")


        # 2.21: Erfaringsbonus-kontrollerne vises i Træningsprogram-topbjælken
        # ved siden af Assistent-knappen. Variablerne oprettes her, fordi resten af
        # appen forventer, at de eksisterer sammen med Spiller-indstillingerne.
        self.ebr_enabled_var = tk.BooleanVar(value=False)
        self.ebr_ratio_var = tk.StringVar(value="1.10")
        self.ebr_knee_var = tk.StringVar(value="linear")
        self.ebr_knee_buttons = []
        self.ebr_knee_canvases = []
        self.ebr_ratio_var.trace_add("write", lambda *_: self._settings_changed("program"))

    def _build_start_stats(self, parent):
        # 2.24/2.39: Egenskaber er integreret i Spiller-sektionen. Rammen er
        # derfor en indvendig frame uden separat LabelFrame-afgrænsning.
        self.start_stats_frame = ttk.Frame(parent)
        if parent is getattr(self, "settings_frame", None):
            self.start_stats_frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        else:
            self.start_stats_frame.pack(fill="x", pady=(0, 10))

        # Hold startstats kompakte og forudsigelige. Brug en fleksibel
        # afslutningskolonne i stedet for at lade spinbox-kolonnerne strække sig.
        for col in range(5):
            self.start_stats_frame.columnconfigure(col, weight=0)
        self.start_stats_frame.columnconfigure(4, weight=1)
        self.start_stat_vars = {}

    def _render_start_stats(self):
        for child in self.start_stats_frame.winfo_children():
            child.destroy()

        self.start_stat_vars = {}
        display_stats = self._vman_stat_display_order(self.current_stats)
        rows_per_column = (len(display_stats) + 1) // 2
        label_padx = getattr(self, "_shared_label_padx", (0, 0))

        for i, stat in enumerate(display_stats):
            row = i % rows_per_column
            col_group = i // rows_per_column
            label_col = col_group * 2
            input_col = label_col + 1

            stat_label_padx = (0, 11) if col_group == 0 else (24, 11)
            ttk.Label(self.start_stats_frame, text=self._display_stat(stat)).grid(
                row=row,
                column=label_col,
                sticky="w",
                padx=stat_label_padx,
                pady=2,
            )
            var = tk.StringVar(value="2")
            self.start_stat_vars[stat] = var
            spin = tk.Spinbox(
                self.start_stats_frame,
                from_=0,
                to=100,
                textvariable=var,
                width=int(getattr(self, "_main_numeric_field_width", 4)),
                command=self._settings_changed,
            )
            self._configure_numeric_widget(spin, integer=True, max_value=100)
            spin.grid(row=row, column=input_col, sticky="w", pady=2)
            var.trace_add("write", lambda *_args, stat=stat: self._on_main_stat_var_changed(stat))

        # 2.28/2.30/2.39: Start-vurderingstal og egenskabssum vises samlet på
        # én kompakt linje, så bunden af Spiller-sektionen ikke bliver rodet af
        # den ekstra avatar-kolonne.
        rating_row = rows_per_column + 1
        ttk.Separator(self.start_stats_frame, orient="horizontal").grid(
            row=rating_row - 1,
            column=0,
            columnspan=5,
            sticky="ew",
            pady=(8, 6),
        )

        summary_frame = ttk.Frame(self.start_stats_frame)
        summary_frame.grid(row=rating_row, column=0, columnspan=5, sticky="w", pady=(0, 1))

        ttk.Label(summary_frame, text="Vurderingstal").pack(side="left")
        self.start_rating_var = tk.StringVar(value="-")
        self.start_rating_label = ttk.Label(
            summary_frame,
            textvariable=self.start_rating_var,
            font=("", 12, "bold"),
        )
        self.start_rating_label.pack(side="left", padx=(8, 24))

        ttk.Label(summary_frame, text="Egenskabssum").pack(side="left")
        self.start_stat_sum_var = tk.StringVar(value="-")
        self.start_stat_sum_label = ttk.Label(
            summary_frame,
            textvariable=self.start_stat_sum_var,
            font=("", 12, "bold"),
        )
        self.start_stat_sum_label.pack(side="left", padx=(8, 0))

        self.player_reset_button = ttk.Button(
            self.start_stats_frame,
            text="Ryd",
            width=6,
            command=self._reset_main_workspace,
        )
        self.player_reset_button.grid(row=rating_row + 1, column=0, columnspan=5, sticky="w", pady=(6, 0))
        self._update_start_rating_label()


    def _sync_session_inner_right_inset(self, event=None):
        """Shorten the inner Session content 4% from right to left."""
        try:
            frame = getattr(self, "program_builder_frame", None)
            if frame is None or not frame.winfo_exists():
                return
            self.update_idletasks()
            inset = max(0, int((frame.winfo_width() or 0) * 0.04))
            if getattr(self, "_session_inner_right_inset_cache", None) == inset:
                return
            self._session_inner_right_inset_cache = inset
            for widget in (
                getattr(self, "dist_frame", None),
                getattr(self, "add_button", None),
                getattr(self, "update_button", None),
                getattr(self, "session_width_spacer", None),
            ):
                if widget is not None and widget.winfo_exists():
                    try:
                        widget.grid_configure(padx=(0, inset))
                    except Exception:
                        pass
        except Exception:
            pass

    def _sync_session_exercise_field_alignment(self, event=None):
        """Keep Øvelse field aligned with the Dage spinbox above it."""
        if getattr(self, "_syncing_exercise_field_alignment", False):
            return
        if not all(hasattr(self, name) for name in ("program_builder_frame", "days_spinbox", "exercise_box")):
            return
        self._syncing_exercise_field_alignment = True
        try:
            # 4.51: Fjern update_idletasks fra denne hyppige alignment. Den
            # kaldtes ved configure og var dyr. Brug eksisterende geometri og cache.
            desired_x = int(self.days_spinbox.winfo_rootx() - self.program_builder_frame.winfo_rootx())
            current = self.exercise_box.grid_info().get("padx", (0, 0))
            current_left = current[0] if isinstance(current, tuple) else int(str(current).split()[0] if str(current).strip() else 0)
            inner_offset = int(self.exercise_box.winfo_rootx() - self.program_builder_frame.winfo_rootx() - int(current_left))
            target_padx = max(0, desired_x - inner_offset)
            cache_key = (desired_x, inner_offset, target_padx)
            if getattr(self, "_exercise_field_alignment_cache", None) == cache_key:
                return
            self._exercise_field_alignment_cache = cache_key
            if int(current_left) != int(target_padx):
                self.exercise_box.grid_configure(padx=(int(target_padx), 0))
        except Exception:
            pass
        finally:
            self._syncing_exercise_field_alignment = False

    def _build_program_builder(self, parent):
        frame = ttk.LabelFrame(parent, text="", padding=8, style="MainSection.TLabelframe")
        self.program_builder_frame = frame
        frame.pack(fill="x", expand=False)
        label_padx = getattr(self, "_shared_label_padx", (0, 0))

        # 3.81: Hele øverste Session-linje er én kompakt række. Det flytter
        # Intensitet og Hård-feltet reelt mod venstre i stedet for at lade
        # grid-kolonnerne skubbe dem mod højre.
        session_top_row = ttk.Frame(frame)
        self.session_top_row = session_top_row
        session_top_row.grid(row=0, column=0, columnspan=4, sticky="w", padx=label_padx)

        ttk.Label(session_top_row, text="Dage").pack(side="left")
        self.days_var = tk.StringVar(value="2")
        self.days_spinbox = tk.Spinbox(
            session_top_row,
            from_=1,
            to=9999,
            textvariable=self.days_var,
            width=int(getattr(self, "_main_numeric_field_width", 4)),
        )
        self._configure_numeric_widget(self.days_spinbox, integer=True, max_value=9999)
        self.days_spinbox.pack(side="left", padx=(28, 34))

        ttk.Label(session_top_row, text="Intensitet").pack(side="left", padx=(0, 8))
        self.intensity_var = tk.StringVar(value=self._display_intensity("Hård"))
        self.intensity_box = self._create_fixed_dropdown(
            session_top_row,
            self.intensity_var,
            values=self._intensity_options(),
            width_px=88,
        )
        self.intensity_box._vman_lowercase_display = False
        self.intensity_box.pack(side="left")
        self.intensity_box.bind("<<ComboboxSelected>>", lambda event: self._on_intensity_change())

        ttk.Label(frame, text="Øvelse").grid(row=1, column=0, sticky="w", padx=label_padx, pady=(6, 0))
        self.exercise_var = tk.StringVar(value="Teknik")
        self.exercise_box = self._create_fixed_dropdown(
            frame,
            self.exercise_var,
            values=[],
            width_px=160,
        )
        # 3.84: Startværdi; efter idle måles Dage-feltets faktiske x-position,
        # så feltet flugter præcist på Windows.
        self.exercise_box.grid(row=1, column=0, columnspan=4, sticky="w", padx=(65, 0), pady=(6, 0))
        self.exercise_box.bind("<<ComboboxSelected>>", lambda event: self._refresh_distribution_inputs())
        self.after_idle(self._sync_session_exercise_field_alignment)
        frame.bind("<Configure>", self._sync_session_exercise_field_alignment, add="+")
        session_top_row.bind("<Configure>", self._sync_session_exercise_field_alignment, add="+")

        self.dist_frame = ttk.LabelFrame(frame, text="Pointfordeling", padding=6)
        self.dist_frame.grid(row=2, column=0, columnspan=4, sticky="new", pady=(4, 0))
        # 2.32: Usynlig breddeholder. Den gør, at Session-panelets modul
        # ikke skifter bredde/horisontal placering, når øvelsens egenskabsnavne
        # har forskellig længde eller Pointfordeling er skjult.
        self.session_width_spacer = ttk.Frame(frame, width=1, height=0)
        self.session_width_spacer.grid(row=5, column=0, columnspan=4, sticky="ew")
        self.session_width_spacer.grid_propagate(False)
        self.dist_vars = {}
        self.dist_box_canvases = {}
        self.dist_auto_locked_stats = set()
        self._suppress_distribution_auto = False

        self.add_button = ttk.Button(frame, text="Tilføj til træningsprogram", command=self._add_phase)
        self.add_button.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(1, 0))

        self.update_button = ttk.Button(frame, text="Opdater", command=self._update_selected_phase)
        self.update_button.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(4, 0))

        self.after_idle(self._sync_session_inner_right_inset)
        # 4.43: Standardøvelsen er nu Teknik.
        # Pointfordeling skal derfor vises korrekt allerede ved opstart.
        self.after_idle(self._refresh_distribution_inputs)
        self.after(50, self._refresh_distribution_inputs)
        frame.bind("<Configure>", self._sync_session_inner_right_inset, add="+")


    # ---------- Main menu / settings ----------

    def _settings_dir(self):
        try:
            base = Path.home() / ".vman_engine"
            base.mkdir(parents=True, exist_ok=True)
            return base
        except Exception:
            base = Path(tempfile.gettempdir()) / "vman_engine"
            base.mkdir(parents=True, exist_ok=True)
            return base

    def _settings_file(self):
        return self._settings_dir() / "settings.json"

    def _resolve_cpu_threads(self):
        """Omsæt CPU-indstillingen til antal CPU-kerner reserveret til Assistenten."""
        total = os.cpu_count() or 1
        value = normalize_cpu_thread_setting(getattr(self, "app_settings", {}).get("cpu_threads", "Auto"))
        if value == "Auto":
            return max(1, int(total) - 2)
        if value == "Maks":
            return max(1, int(total))
        try:
            # "1 kerne", "2 kerner", "4 kerner"
            requested = int(value.split()[0])
        except Exception:
            requested = 1
        return max(1, min(int(requested), int(total)))

    def _assistant_effective_worker_count(self, search_method, requested_workers):
        """Returnér det antal worker-processer Assistenten faktisk bør bruge.

        Ved 2+ valgte CPU-kerner holdes én kerne fri til Tkinter/UI-tråden og
        løbende resultatvisning. Resten bruges nu også af de adaptive søgemetoder.
        Stabiliteten kommer fra sharding af seed-kandidater, separat RNG-seed pr.
        worker og løbende sammenfletning af topresultater i UI-tråden.
        """
        try:
            requested_workers = max(1, int(requested_workers or 1))
        except Exception:
            requested_workers = 1
        if requested_workers >= 2:
            return max(1, requested_workers - 1)
        return requested_workers

    def _assistant_combine_result_list(self, results, top_n=10):
        combined = list(results or [])
        combined.sort(key=lambda item: (item.score, item.average), reverse=True)
        seen = set()
        unique = []
        for result in combined:
            try:
                signature = self._assistant_result_signature(result)
            except Exception:
                signature = repr(result)
            if signature in seen:
                continue
            seen.add(signature)
            unique.append(result)
            if len(unique) >= top_n:
                break
        for index, result in enumerate(unique, start=1):
            result.rank = index
        return unique

    def _assistant_parallel_timed_search(
        self,
        *,
        snapshot,
        default_position,
        xp,
        reference_points,
        seed_programs,
        progress_callback,
        worker_count,
    ):
        """Kør Assistent-søgning i flere processer, så CPU-valget bruges reelt."""
        import concurrent.futures
        import multiprocessing

        position = internal_position(snapshot.get("position", default_position))
        base_search_time = snapshot.get("search_time", "5 min")
        manual_mode = str(base_search_time) == "Manuel" or snapshot.get("search_method") == "Brute Force"
        duration = max(0.05, float(search_time_to_seconds(base_search_time)))
        worker_count = max(1, int(worker_count or 1))
        started = time.monotonic()
        deadline = started + duration
        all_results = []

        common_kwargs = dict(
            position=position,
            start_stats=snapshot.get("start_stats", {}),
            start_age=float(snapshot.get("start_age", 15.0)),
            end_age=float(snapshot.get("end_age", 26.0)),
            xp=xp,
            weights=snapshot.get("weights", POSITION_WEIGHTS[position]),
            change_every_days=int(snapshot.get("change_every_days", 3)),
            search_method=snapshot.get("search_method", "Beam Search"),
            xp_variance=bool(snapshot.get("xp_variance", False)),
            reference_training_points=reference_points,
            experience_bonus_enabled=bool(snapshot.get("ebr_enabled", False)),
            experience_bonus_ratio=float(snapshot.get("ebr_ratio", 1.10)),
            experience_bonus_knee=snapshot.get("ebr_knee", "linear"),
            allow_intensity_boost=bool(snapshot.get("allow_intensity_boost", False)),
            intensity_boost_uses=int(snapshot.get("intensity_boost_uses", 1)),
            intensity_boost_period_days=int(snapshot.get("intensity_boost_period_days", 7)),
            group_high_weighted_stats=bool(snapshot.get("group_high_weighted_stats", True)),
            penalty_tolerance=snapshot.get("penalty_tolerance", "Høj"),
            roll_enabled=bool(snapshot.get("roll_enabled", False)),
            roll_block_days=int(snapshot.get("roll_block_days", 7)),
            training_match_prelude_enabled=bool(snapshot.get("training_match_prelude_enabled", False)),
            training_match_until_age=snapshot.get("training_match_until_age", None),
            top_n=10,
            progress_callback=None,
            stop_event=None,
            time_limit_seconds=duration,
            seed_programs=seed_programs,
        )

        manager = multiprocessing.Manager()
        process_stop_event = manager.Event()
        progress_queue = manager.Queue()
        common_kwargs["stop_event"] = process_stop_event

        executor = concurrent.futures.ProcessPoolExecutor(max_workers=worker_count)
        futures = []
        future_to_index = {}
        worker_results_by_index = {}
        worker_evaluated_by_index = {}

        def drain_worker_progress(max_items=200):
            drained = 0
            while drained < max_items:
                try:
                    kind, payload = progress_queue.get_nowait()
                except queue.Empty:
                    break
                except Exception:
                    break
                drained += 1
                if kind != "progress" or not isinstance(payload, dict):
                    continue
                try:
                    idx = int(payload.get("worker_index", -1))
                except Exception:
                    idx = -1
                if idx < 0:
                    continue
                worker_results_by_index[idx] = list(payload.get("results") or [])
                try:
                    worker_evaluated_by_index[idx] = max(0, int(payload.get("evaluated", 0)))
                except Exception:
                    pass
            return drained

        def live_combined_results():
            live_results = list(all_results)
            for worker_results in worker_results_by_index.values():
                live_results.extend(worker_results or [])
            return self._assistant_combine_result_list(live_results, top_n=10)

        def publish_parallel_progress(force=False):
            nonlocal next_progress
            if progress_callback is None:
                return
            now = time.monotonic()
            if not force and now < next_progress:
                return
            combined = live_combined_results()
            progress_callback({
                "elapsed": max(0.0, now - started),
                "remaining": max(0.0, deadline - now),
                "evaluated": sum(worker_evaluated_by_index.values()),
                "best_rating": combined[0].rating if combined else None,
                "results": combined,
                "threads": worker_count,
                "manual": manual_mode,
            })
            next_progress = now + 0.75

        try:
            for index in range(worker_count):
                kwargs = dict(common_kwargs)
                # Søgetiden sendes som unik tekst pr. proces, så optimizerens RNG-seed
                # ikke bliver identisk på tværs af processer. Brute Force bruger
                # samtidig sharding, så processerne ikke tester samme kombinationer.
                kwargs["search_time"] = base_search_time if manual_mode else f"{base_search_time} · proces {index + 1}"
                kwargs["brute_force_shard_index"] = index
                kwargs["brute_force_shard_count"] = worker_count
                future = executor.submit(_assistant_parallel_worker_entry, kwargs, progress_queue, index)
                futures.append(future)
                future_to_index[future] = index

            next_progress = 0.0
            pending = set(futures)
            while pending:
                drain_worker_progress()
                if getattr(self, "assistant_sim_stop_event", None) is not None and self.assistant_sim_stop_event.is_set():
                    try:
                        process_stop_event.set()
                    except Exception:
                        pass
                    for future in pending:
                        future.cancel()
                    break

                done, pending = concurrent.futures.wait(
                    pending,
                    timeout=0.35,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done:
                    idx = future_to_index.get(future)
                    worker_results = list(future.result() or [])
                    all_results.extend(worker_results)
                    if idx is not None:
                        worker_results_by_index[idx] = worker_results

                publish_parallel_progress()

            drain_worker_progress(max_items=1000)
            combined = live_combined_results()
            publish_parallel_progress(force=True)
            return combined
        finally:
            stopped = getattr(self, "assistant_sim_stop_event", None) is not None and self.assistant_sim_stop_event.is_set()
            if stopped:
                try:
                    process_stop_event.set()
                except Exception:
                    pass
            executor.shutdown(wait=True, cancel_futures=True)
            try:
                manager.shutdown()
            except Exception:
                pass


    def _load_app_settings(self):
        settings = copy.deepcopy(DEFAULT_APP_SETTINGS)
        try:
            path = self._settings_file()
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    for key in DEFAULT_APP_SETTINGS:
                        if key in loaded:
                            settings[key] = loaded[key]
        except Exception:
            pass

        settings["cpu_threads"] = normalize_cpu_thread_setting(settings.get("cpu_threads", "Auto"))
        if settings.get("cpu_threads") not in CPU_THREAD_OPTIONS:
            settings["cpu_threads"] = "Auto"
        if settings.get("language") not in ("", "da", "en"):
            settings["language"] = ""
        for key in ("auto_template", "warn_overwrite", "autosave", "auto_backup"):
            settings[key] = bool(settings.get(key, DEFAULT_APP_SETTINGS[key]))
        try:
            settings["ui_scale"] = max(0.75, min(1.40, float(settings.get("ui_scale", 1.0))))
        except Exception:
            settings["ui_scale"] = 1.0
        if not isinstance(settings.get("ui_text_overrides"), dict):
            settings["ui_text_overrides"] = {}
        else:
            cleaned_overrides = {}
            for key, value in settings.get("ui_text_overrides", {}).items():
                if str(key).strip() and str(value).strip():
                    cleaned_overrides[str(key)] = str(value)
            settings["ui_text_overrides"] = cleaned_overrides
        settings["group_import_debug_dir"] = str(settings.get("group_import_debug_dir", "") or "")
        return settings

    def _save_app_settings(self):
        try:
            path = self._settings_file()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.app_settings, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            self._main_notice(f"Kunne ikke gemme indstillinger: {e}")
            return False

    def _setting_bool(self, key, default=False):
        try:
            return bool(self.app_settings.get(key, default))
        except Exception:
            return bool(default)

    def _language_code(self):
        code = str(getattr(self, "app_settings", {}).get("language", "") or "da")
        return code if code in ("da", "en") else "da"

    def _is_windows_ui(self):
        try:
            return str(self.tk.call("tk", "windowingsystem")).lower() == "win32"
        except Exception:
            return os.name == "nt"

    def _tr(self, value):
        text = str(value)
        if self._language_code() == "en":
            return UI_TEXT_DA_TO_EN.get(text, text)
        return UI_TEXT_EN_TO_DA.get(text, text)

    def _apply_custom_ui_text(self, source_text, translated_text=None):
        translated_text = str(source_text if translated_text is None else translated_text)
        try:
            overrides = getattr(self, "app_settings", {}).get("ui_text_overrides", {}) or {}
            # Prefer the original Danish/source key, but also allow editing text that
            # is already translated or custom. This keeps user edits stable across
            # normal language refreshes.
            return str(overrides.get(str(source_text), overrides.get(translated_text, translated_text)))
        except Exception:
            return translated_text

    def _display_stat(self, stat):
        return display_stat(stat, self._language_code())

    def _display_exercise(self, exercise):
        return display_exercise(exercise, self._language_code())

    def _display_intensity(self, intensity):
        return display_intensity(intensity, self._language_code())

    def _display_position(self, position):
        return display_position(position, self._language_code())

    def _display_domain_text(self, value):
        """Translate stored Danish domain/result text for display without changing saved data."""
        text = str(value)
        if self._language_code() != "en" or not text:
            return text

        replacements = []
        replacements.extend(sorted(EXERCISE_DA_TO_EN.items(), key=lambda item: len(item[0]), reverse=True))
        replacements.extend(sorted(STAT_DA_TO_EN.items(), key=lambda item: len(item[0]), reverse=True))
        replacements.extend(sorted(INTENSITY_DA_TO_EN.items(), key=lambda item: len(item[0]), reverse=True))
        replacements.extend(sorted(POSITION_DA_TO_EN.items(), key=lambda item: len(item[0]), reverse=True))
        replacements.extend(sorted(PENALTY_TOLERANCE_DA_TO_EN.items(), key=lambda item: len(item[0]), reverse=True))
        replacements.extend(sorted(SEARCH_TIME_DA_TO_EN.items(), key=lambda item: len(item[0]), reverse=True))
        replacements.extend([
            # Longer method/result phrases first, so compound labels are translated cleanly.
            ("Træningskamp til", "Training match until"),
            ("TM til", "Training match until"),
            ("Topøvelser", "Top exercises"),
            ("Vurderingstal", "Rating"),
            ("Vurdering", "Rating"),
            ("Gennemsnit", "Average"),
            ("Stat sum", "Total"),
            ("Egenskabssum", "Total"),
            ("Træningsperiode", "Training period"),
            ("Træningskamp", "Training match"),
            ("Blok", "Cycle"),
            ("blok", "cycle"),
            ("Gruppe", "Group"),
            ("Straftolerance", "Penalty tolerance"),
            ("Tillad intensitetsforøgelse", "Allow intensity boost"),
            ("Fast rul", "Fixed rotation"),
            ("Søgetid", "Search time"),
            ("Metode", "Method"),
            ("Indlæst", "Loaded"),
            ("Udgangspunkt", "Starting point"),
            ("skift hver", "change every"),
            ("Skift hver", "Change every"),
            ("Kun", "Only"),
            ("kun", "only"),
            ("kandidat", "candidate"),
            ("videre", "continued"),
            ("Rul", "Rotation"),
            ("hver", "every"),
            ("dage", "days"),
            ("dag", "day"),
            ("år", "years"),
            ("frem til", "until"),
            ("ingen resultater endnu", "no results yet"),
        ])
        for src, dst in replacements:
            text = text.replace(src, dst)
        return text

    def _core_text(self, core_count):
        try:
            count = int(core_count)
        except Exception:
            count = 1
        if self._language_code() == "en":
            return "1 core" if count == 1 else f"{count} cores"
        return "1 kerne" if count == 1 else f"{count} kerner"

    def _position_options(self):
        return DISPLAY_POSITIONS_EN if self._language_code() == "en" else DISPLAY_POSITIONS_DA

    def _intensity_options(self):
        return [self._display_intensity(value) for value in INTENSITY_OPTIONS.keys()]

    def _exercise_options_for_position(self, position):
        internal = internal_position(position)
        return [self._display_exercise(value) for value in exercises_for_position(internal).keys()]

    def _search_time_options(self):
        values = ["10 sek", "1 min", "5 min", "15 min", "1 time", "4 timer", "8 timer", "Manuel"]
        return [display_search_time(value, self._language_code()) for value in values]

    def _penalty_tolerance_options(self):
        values = ["Ingen", "Lav", "Mellem", "Høj"]
        return [display_penalty_tolerance(value, self._language_code()) for value in values]

    def _refresh_language_sensitive_controls(self):
        # Keep internal data Danish, but present dropdowns/tables in the chosen UI language.
        try:
            if hasattr(self, "position_box") and hasattr(self, "position_var"):
                pos = internal_position(self.position_var.get())
                self.position_box.configure(values=self._position_options())
                self.position_var.set(self._display_position(pos))
        except Exception:
            pass

        for var_name, box_name in (
            ("xp_reference_intensity_var", "xp_reference_intensity_box"),
            ("intensity_var", "intensity_box"),
            ("assistant_xp_reference_intensity_var", "assistant_xp_reference_intensity_box"),
        ):
            try:
                var = getattr(self, var_name, None)
                box = getattr(self, box_name, None)
                if var is not None:
                    current = internal_intensity(var.get())
                    var.set(self._display_intensity(current))
                if box is not None:
                    if getattr(box, "_vman_is_fixed_intensity_dropdown", False):
                        try:
                            self._redraw_fixed_intensity_dropdown(box)
                        except Exception:
                            pass
                    else:
                        box.configure(values=self._intensity_options())
            except Exception:
                pass

        try:
            self._sync_intensity_suffix_labels()
        except Exception:
            pass

        try:
            if hasattr(self, "exercise_box") and hasattr(self, "exercise_var"):
                position = internal_position(self.position_var.get()) if hasattr(self, "position_var") else getattr(self, "active_position", "Keepere")
                self._refresh_session_exercise_options(position, reset_if_invalid=False)
        except Exception:
            pass

        try:
            if hasattr(self, "assistant_position_var"):
                pos = internal_position(self.assistant_position_var.get())
                self.assistant_position_var.set(self._display_position(pos))
        except Exception:
            pass

        try:
            if hasattr(self, "assistant_search_time_box"):
                current = internal_search_time(self.assistant_search_time_var.get())
                self.assistant_search_time_box.configure(values=self._search_time_options())
                self.assistant_search_time_var.set(display_search_time(current, self._language_code()))
        except Exception:
            pass

        try:
            if hasattr(self, "assistant_penalty_tolerance_box"):
                current = internal_penalty_tolerance(self.assistant_penalty_tolerance_var.get())
                self.assistant_penalty_tolerance_box.configure(values=self._penalty_tolerance_options())
                self.assistant_penalty_tolerance_var.set(display_penalty_tolerance(current, self._language_code()))
        except Exception:
            pass

        try:
            if hasattr(self, "dist_frame"):
                self._refresh_distribution_inputs()
        except Exception:
            pass

        try:
            self._refresh_program_tree()
        except Exception:
            pass

        try:
            self._redraw_live_stats_panel()
        except Exception:
            pass

        try:
            total_days = sum(item.days for item in getattr(self, "program", []))
            if getattr(self, "last_summary", None):
                position = internal_position(getattr(self, "active_position", self.position_var.get() if hasattr(self, "position_var") else "Keepere"))
                rating = self.last_summary.get(f"Vurderingstal_{position}")
                average = self.last_summary.get("Gennemsnit")
                end_age = calculate_end_age(float(self.start_age_var.get()), total_days) if hasattr(self, "start_age_var") else None
                self._set_status_line(total_days, end_age=end_age, rating=rating, average=average)
            else:
                self._set_status_line(total_days)
        except Exception:
            pass

    def _translate_widget_tree(self, parent=None):
        parent = parent or self

        def apply_one(widget):
            try:
                current = widget.cget("text")
                if isinstance(current, str) and current:
                    key = getattr(widget, "_vman_i18n_key", None)
                    if not key:
                        key = current
                        try:
                            setattr(widget, "_vman_i18n_key", key)
                        except Exception:
                            pass
                    widget.configure(text=self._tr(key))
            except Exception:
                pass

            # Toplevel title
            try:
                if isinstance(widget, tk.Toplevel):
                    title = widget.title()
                    if title:
                        widget.title(self._tr(title))
            except Exception:
                pass

            # Notebook tab titles
            try:
                if widget.winfo_class() == "TNotebook":
                    for tab_id in widget.tabs():
                        tab_text = widget.tab(tab_id, "text")
                        if tab_text:
                            tab_keys = getattr(widget, "_vman_i18n_tab_keys", {})
                            key = tab_keys.get(tab_id, tab_text)
                            tab_keys[tab_id] = key
                            try:
                                setattr(widget, "_vman_i18n_tab_keys", tab_keys)
                            except Exception:
                                pass
                            widget.tab(tab_id, text=self._tr(key))
            except Exception:
                pass

            for child in widget.winfo_children():
                apply_one(child)

        apply_one(parent)

    def _apply_language_to_menus(self):
        def set_labels(menu, labels_by_index):
            if menu is None:
                return
            for index, label in labels_by_index.items():
                try:
                    menu.entryconfig(index, label=self._tr(label))
                except Exception:
                    pass

        try:
            labels = ["Indstillinger", "Hjælp", "Donation", "Om VMAN Training Planner"]
            set_labels(self.main_menu, {index: label for index, label in enumerate(labels)})
        except Exception:
            pass

        try:
            set_labels(getattr(self, "presets_menu", None), {5: "Gem som skabelon...", 6: "Indlæs skabelon fra fil..."})
            set_labels(getattr(self, "export_menu", None), {0: "Historik som CSV", 1: "Rapport som PDF", 2: "Rapport som DOC"})
            set_labels(getattr(self, "assistant_result_menu", None), {
                0: "Importer til kontrolcenter",
                2: "Eksportér",
                4: "Gensimulér med XP-variation",
                6: "Søg videre med",
            })
            set_labels(getattr(self, "assistant_result_export_submenu", None), {0: "Rapport som PDF", 1: "Rapport som DOC", 2: "Historik som CSV"})
            set_labels(getattr(self, "assistant_export_button_menu", None), {0: "Rapport som PDF", 1: "Rapport som DOC", 2: "Historik som CSV"})
            set_labels(getattr(self, "program_context_menu", None), {
                0: "Redigér valgte",
                1: "Opløs blok",
                3: "Kopiér valgte",
                4: "Indsæt efter valgte",
                5: "Duplikér valgte",
                7: "Flyt valgte op",
                8: "Flyt valgte ned",
                10: "Tilføj valgte til blok",
                12: "Slet valgte",
            })
        except Exception:
            pass

    def _update_program_tree_column_separators(self, event=None):
        tree = getattr(self, "program_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        try:
            columns = ("delete", "days", "exercise", "intensity", "distribution")
            total_width = int(tree.winfo_width() or 0)
            widths = tuple(int(tree.column(col, "width")) for col in columns)
            cache_key = (total_width, widths)
            if getattr(self, "_program_tree_header_border_cache", None) == cache_key:
                return
            self._program_tree_header_border_cache = cache_key
            border_lines = getattr(self, "program_tree_header_borders", None)
            if border_lines is None:
                border_lines = []
                self.program_tree_header_borders = border_lines
            while len(border_lines) < len(columns):
                cell = {
                    "top": tk.Frame(tree, bg=getattr(self, "_theme_panel_fg", "#f0f0f0"), height=1, bd=0, highlightthickness=0),
                    "bottom": tk.Frame(tree, bg=getattr(self, "_theme_panel_fg", "#f0f0f0"), height=1, bd=0, highlightthickness=0),
                    "left": tk.Frame(tree, bg=getattr(self, "_theme_panel_fg", "#f0f0f0"), width=1, bd=0, highlightthickness=0),
                    "right": tk.Frame(tree, bg=getattr(self, "_theme_panel_fg", "#f0f0f0"), width=1, bd=0, highlightthickness=0),
                }
                border_lines.append(cell)
            x = 0
            heading_height = 26
            for idx, col in enumerate(columns):
                width = int(tree.column(col, "width"))
                if idx == len(columns) - 1 and total_width > x:
                    # 4.42: Sidste header-boks (Sessions) skal række helt ud
                    # til højre afgrænsning, også hvis Treeview måler en smule
                    # bredere end summen af kolonnebredderne.
                    width = max(width, total_width - x)
                cell = border_lines[idx]
                left_x = x
                right_x = x + width - 1
                cell["top"].place(x=left_x, y=0, width=width, height=1)
                cell["bottom"].place(x=left_x, y=heading_height - 1, width=width, height=1)
                cell["left"].place(x=left_x, y=0, width=1, height=heading_height)
                cell["right"].place(x=right_x, y=0, width=1, height=heading_height)
                for part in cell.values():
                    try:
                        part.lift()
                    except Exception:
                        pass
                x += width
        except Exception:
            pass

    def _apply_language_to_tree_headings(self):
        try:
            if hasattr(self, "program_tree"):
                headings = {
                    "days": "Dage",
                    "exercise": "Øvelse",
                    "intensity": "Intensitet",
                    "distribution": "Sessions",
                }
                for col, label in headings.items():
                    self._tree_heading_text_keys[(str(self.program_tree), col)] = label
                    self.program_tree.heading(col, text=self._tr(label))
        except Exception:
            pass

        try:
            if hasattr(self, "assistant_top_tree"):
                headings = {
                    "rank": "#",
                    "score": "VT",
                    "uw_score": "bVT",
                    "avg": "Gns.",
                    "method": "Metode",
                    "notes": "Sessions",
                }
                for col, label in headings.items():
                    try:
                        self._tree_heading_text_keys[(str(self.assistant_top_tree), col)] = label
                        self.assistant_top_tree.heading(col, text=self._tr(label))
                    except Exception:
                        pass
        except Exception:
            pass

    def _apply_language_to_current_ui(self):
        old_flag = getattr(self, "_applying_app_settings", False)
        self._applying_app_settings = True
        try:
            self._translate_widget_tree(self)
            self._apply_language_to_menus()
            self._apply_language_to_tree_headings()
            self._refresh_language_sensitive_controls()
            try:
                self._position_main_section_title_overlays()
            except Exception:
                pass

            try:
                if hasattr(self, "assistant_title_var"):
                    current = self.assistant_title_var.get()
                    if current:
                        self.assistant_title_var.set(self._tr(current))
            except Exception:
                pass
        finally:
            self._applying_app_settings = old_flag

    def _maybe_prompt_language(self):
        if os.environ.get("VMAN_ENGINE_SKIP_LANGUAGE_POPUP"):
            return
        try:
            if self.app_settings.get("language"):
                return
            self._prompt_first_language()
        except Exception:
            pass

    def _draw_danish_flag(self, canvas, x, y, w=42, h=26):
        canvas.create_rectangle(x, y, x + w, y + h, fill="#c60c30", outline="#aaaaaa")
        canvas.create_rectangle(x + int(w * 0.28), y, x + int(w * 0.42), y + h, fill="#ffffff", outline="")
        canvas.create_rectangle(x, y + int(h * 0.42), x + w, y + int(h * 0.58), fill="#ffffff", outline="")

    def _draw_uk_half_flag(self, canvas, x, y, w, h):
        canvas.create_rectangle(x, y, x + w, y + h, fill="#012169", outline="")
        canvas.create_line(x, y, x + w, y + h, fill="#ffffff", width=5)
        canvas.create_line(x, y + h, x + w, y, fill="#ffffff", width=5)
        canvas.create_line(x, y, x + w, y + h, fill="#c8102e", width=2)
        canvas.create_line(x, y + h, x + w, y, fill="#c8102e", width=2)
        canvas.create_rectangle(x + w // 2 - 3, y, x + w // 2 + 3, y + h, fill="#ffffff", outline="")
        canvas.create_rectangle(x, y + h // 2 - 3, x + w, y + h // 2 + 3, fill="#ffffff", outline="")
        canvas.create_rectangle(x + w // 2 - 1, y, x + w // 2 + 1, y + h, fill="#c8102e", outline="")
        canvas.create_rectangle(x, y + h // 2 - 1, x + w, y + h // 2 + 1, fill="#c8102e", outline="")

    def _draw_us_half_flag(self, canvas, x, y, w, h):
        stripe_h = max(1, h / 13)
        for i in range(13):
            color = "#b22234" if i % 2 == 0 else "#ffffff"
            canvas.create_rectangle(x, y + i * stripe_h, x + w, y + (i + 1) * stripe_h, fill=color, outline="")
        canton_w = int(w * 0.52)
        canton_h = int(stripe_h * 7)
        canvas.create_rectangle(x, y, x + canton_w, y + canton_h, fill="#3c3b6e", outline="")
        for row in range(3):
            for col in range(4):
                cx = x + 3 + col * max(3, canton_w // 4)
                cy = y + 3 + row * max(3, canton_h // 3)
                canvas.create_oval(cx, cy, cx + 1, cy + 1, fill="#ffffff", outline="")

    def _draw_split_uk_us_flag(self, canvas, x, y, w=42, h=26):
        left_w = w // 2
        self._draw_uk_half_flag(canvas, x, y, left_w, h)
        self._draw_us_half_flag(canvas, x + left_w, y, w - left_w, h)
        canvas.create_line(x + left_w, y, x + left_w, y + h, fill="#ffffff", width=1)
        canvas.create_rectangle(x, y, x + w, y + h, outline="#aaaaaa")

    def _prompt_first_language(self):
        win = tk.Toplevel(self)
        win.title("Vælg sprog / Choose language")
        win.transient(self)
        win.resizable(False, False)

        outer = ttk.Frame(win, padding=18)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text="Vælg sprog",
            font=("", 14, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        def choose(code):
            self.app_settings["language"] = code
            self._save_app_settings()
            self._apply_language_to_current_ui()
            win.destroy()

        for code, label in (("da", "Dansk"), ("en", "English")):
            row = ttk.Frame(outer)
            row.pack(fill="x", pady=4)

            flag = tk.Canvas(row, width=48, height=30, highlightthickness=0, bd=0)
            flag.pack(side="left", padx=(0, 10))
            if code == "da":
                self._draw_danish_flag(flag, 3, 2, 42, 26)
            else:
                self._draw_split_uk_us_flag(flag, 3, 2, 42, 26)

            ttk.Button(
                row,
                text=label,
                width=18,
                command=lambda code=code: choose(code),
            ).pack(side="left", fill="x", expand=True)

        try:
            win.update_idletasks()
            x = self.winfo_rootx() + max(0, (self.winfo_width() - win.winfo_width()) // 2)
            y = self.winfo_rooty() + max(0, (self.winfo_height() - win.winfo_height()) // 3)
            win.geometry(f"+{x}+{y}")
            win.grab_set()
            win.lift()
            win.focus_force()
        except Exception:
            pass

    def _handle_main_menu_action(self, name):
        if name in {"Indstillinger", "Settings"}:
            self._open_settings_window()
            return
        if name in {"Hjælp", "Help"}:
            self._open_user_manual()
            return
        if name == "Donation":
            self._open_donation_window()
            return
        if name in {"Om VMAN Training Planner", "About VMAN Training Planner"}:
            self._open_about_window()
            return
        return

    def _open_user_manual(self):
        language = str(self.app_settings.get("language") or "da").lower()
        filename = (
            "VMAN_Training_Planner_User_Manual.pdf"
            if language == "en"
            else "VMAN_Training_Planner_Brugermanual.pdf"
        )
        manual_path = Path(__file__).resolve().parent / "manuals" / filename

        if not manual_path.is_file():
            messagebox.showerror(
                "VMAN Training Planner",
                self._tr("Brugermanualen kunne ikke findes."),
                parent=self,
            )
            return

        try:
            if os.name == "nt":
                os.startfile(str(manual_path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(manual_path)])
            else:
                subprocess.Popen(["xdg-open", str(manual_path)])
        except Exception:
            messagebox.showerror(
                "VMAN Training Planner",
                self._tr("Kunne ikke åbne brugermanualen."),
                parent=self,
            )

    def _open_about_window(self):
        try:
            if self.about_window is not None and self.about_window.winfo_exists():
                self.about_window.lift()
                self.about_window.focus_force()
                return
        except Exception:
            self.about_window = None

        win = tk.Toplevel(self)
        win.withdraw()
        self.about_window = win
        win.title("Om VMAN Training Planner")
        win.geometry("560x360")
        win.minsize(520, 320)
        win.transient(self)
        win.protocol("WM_DELETE_WINDOW", lambda: self._close_aux_window("about"))

        outer = ttk.Frame(win, padding=18)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text="VMAN Training Planner",
            font=("", 20, "bold"),
        ).pack(anchor="w", pady=(0, 14))

        body = (
            "Et uofficielt værktøj til at planlægge og simulere træning i Virtual Manager.\n\n"
            "Udviklet af Niklas Schmidt - FC Dronningemaen\n"
            "Med teknisk hjælp fra ChatGPT\n\n"
            "Tak til alle der tester, finder fejl og kommer med idéer.\n\n"
            "Version: 1.00\n\n"
            "VMAN Training Planner er ikke tilknyttet, godkendt af eller officielt forbundet med Virtual Manager."
        )

        ttk.Label(
            outer,
            text=body,
            wraplength=500,
            justify="left",
        ).pack(anchor="w", fill="x")

        ttk.Button(
            outer,
            text="Luk",
            command=lambda: self._close_aux_window("about"),
        ).pack(anchor="e", pady=(18, 0))

        try:
            win.update_idletasks()
            x = self.winfo_rootx() + max(0, (self.winfo_width() - win.winfo_width()) // 2)
            y = self.winfo_rooty() + max(0, (self.winfo_height() - win.winfo_height()) // 3)
            win.geometry(f"+{x}+{y}")
            self._translate_widget_tree(win)
            win.title(self._tr("Om VMAN Training Planner"))
            win.deiconify()
            win.lift()
            win.focus_force()
        except Exception:
            try:
                win.deiconify()
            except Exception:
                pass

    def _open_donation_window(self):
        try:
            if self.donation_window is not None and self.donation_window.winfo_exists():
                self.donation_window.lift()
                self.donation_window.focus_force()
                return
        except Exception:
            self.donation_window = None

        url = "https://qr.mobilepay.dk/box/06e34e6a-c90d-49e4-88d6-3d94bd8f519e/pay-in"
        win = tk.Toplevel(self)
        win.withdraw()
        self.donation_window = win
        win.title("Donation")
        win.transient(self)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", lambda: self._close_aux_window("donation"))

        outer = ttk.Frame(win, padding=12)
        outer.pack(fill="both", expand=True)

        asset_path = Path(__file__).resolve().parent / "assets" / "donation_mobilepay.png"
        photo = None
        original_photo = None
        if asset_path.exists():
            try:
                original_photo = tk.PhotoImage(file=str(asset_path))
                # 3.06: Donationsvinduet skal være halv størrelse.
                photo = original_photo.subsample(2, 2)
            except Exception:
                photo = None
                original_photo = None

        self._donation_photo_original = original_photo
        self._donation_photo = photo

        if photo is not None:
            image_label = tk.Label(outer, image=photo, bd=0, highlightthickness=0, cursor="hand2")
            image_label.pack()
            image_label.bind("<Button-1>", lambda event: webbrowser.open_new_tab(url))
        else:
            fallback = ttk.Frame(outer, padding=16)
            fallback.pack(fill="both", expand=True)
            ttk.Label(fallback, text="Donation", font=("", 16, "bold")).pack(pady=(0, 8))
            ttk.Button(fallback, text=self._tr("Åbn MobilePay-link"), command=lambda: webbrowser.open_new_tab(url)).pack()

        try:
            win.update_idletasks()
            x = self.winfo_rootx() + max(0, (self.winfo_width() - win.winfo_width()) // 2)
            y = self.winfo_rooty() + max(0, (self.winfo_height() - win.winfo_height()) // 3)
            win.geometry(f"+{x}+{y}")
            self._translate_widget_tree(win)
            win.title(self._tr("Donation"))
            win.deiconify()
            win.lift()
            win.focus_force()
        except Exception:
            try:
                win.deiconify()
            except Exception:
                pass

    def _close_aux_window(self, name):
        target = None
        if name == "donation":
            target = getattr(self, "donation_window", None)
            self.donation_window = None
            self._donation_photo = None
            self._donation_photo_original = None
        elif name == "about":
            target = getattr(self, "about_window", None)
            self.about_window = None
        if target is not None:
            try:
                target.destroy()
            except Exception:
                pass

    def _open_settings_window(self):
        try:
            if self.settings_window is not None and self.settings_window.winfo_exists():
                self.settings_window.lift()
                self.settings_window.focus_force()
                return
        except Exception:
            self.settings_window = None

        win = tk.Toplevel(self)
        win.withdraw()
        self.settings_window = win
        win.title("Indstillinger")
        win.geometry("520x470")
        win.minsize(500, 430)
        win.transient(self)

        outer = ttk.Frame(win, padding=12)
        outer.pack(fill="both", expand=True)

        title_row = ttk.Frame(outer)
        title_row.pack(fill="x", pady=(0, 10))
        ttk.Label(title_row, text="Indstillinger", font=("", 16, "bold")).pack(side="left")

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)

        general = ttk.Frame(notebook, padding=12)
        safety = ttk.Frame(notebook, padding=12)
        save_tab = ttk.Frame(notebook, padding=12)
        performance = ttk.Frame(notebook, padding=12)
        notebook.add(general, text="Generelt")
        notebook.add(safety, text="Advarsler")
        notebook.add(save_tab, text="Gem")
        notebook.add(performance, text="Ydeevne")

        self._settings_vars = {
            "language": tk.StringVar(value=LANGUAGE_LABELS.get(self.app_settings.get("language") or "da", "Dansk")),
            "auto_template": tk.BooleanVar(value=self._setting_bool("auto_template", True)),
            "warn_overwrite": tk.BooleanVar(value=self._setting_bool("warn_overwrite", False)),
            "autosave": tk.BooleanVar(value=self._setting_bool("autosave", True)),
            "auto_backup": tk.BooleanVar(value=self._setting_bool("auto_backup", True)),
            "cpu_threads": tk.StringVar(value=display_cpu_thread(self.app_settings.get("cpu_threads", "Auto"), self._language_code())),
        }

        ttk.Label(general, text="Sprog", font=("", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        lang_box = self._create_fixed_dropdown(
            general,
            self._settings_vars["language"],
            values=("Dansk", "English"),
            width_px=176,
        )
        lang_box.grid(row=1, column=0, sticky="w", pady=(0, 12))
        ttk.Label(
            general,
            text="Vælg Dansk eller English som programsprog.",
            wraplength=440,
        ).grid(row=2, column=0, sticky="w", pady=(0, 16))

        ttk.Checkbutton(
            general,
            text="Autoskabelon",
            variable=self._settings_vars["auto_template"],
        ).grid(row=3, column=0, sticky="w")
        ttk.Label(
            general,
            text="Skifter automatisk til den matchende Sheikh-skabelon ved positionsskift/spillerimport.",
            wraplength=440,
        ).grid(row=4, column=0, sticky="w", pady=(2, 0))

        ttk.Checkbutton(
            safety,
            text="Advarsel ved overskrivning",
            variable=self._settings_vars["warn_overwrite"],
        ).pack(anchor="w", pady=(0, 8))
        ttk.Label(
            safety,
            text="Vis advarsel før handlinger der kan overskrive nuværende data:",
            wraplength=440,
        ).pack(anchor="w", pady=(0, 6))
        for line in (
            "• indlæsning af ny spiller",
            "• skift af skabelon",
            "• nulstilling af træningsprogram",
            "• overskrivning af gemt træningsprogram eller Assistent-søgning",
        ):
            ttk.Label(safety, text=line).pack(anchor="w", padx=(12, 0), pady=1)
        ttk.Label(
            safety,
            text="Standard er slået fra.",
            wraplength=440,
        ).pack(anchor="w", pady=(14, 0))

        ttk.Checkbutton(
            save_tab,
            text="Autosave",
            variable=self._settings_vars["autosave"],
        ).pack(anchor="w", pady=(0, 6))
        ttk.Label(
            save_tab,
            text="Gemmer løbende seneste arbejdsstatus i en autosave-fil.",
            wraplength=440,
        ).pack(anchor="w", pady=(0, 14))

        ttk.Checkbutton(
            save_tab,
            text="Automatisk gem backup",
            variable=self._settings_vars["auto_backup"],
        ).pack(anchor="w", pady=(0, 6))
        ttk.Label(
            save_tab,
            text="Gemmer historiske backup-kopier ved ændringer. Bevares adskilt fra Autosave, så seneste tilstand og backup-historik kan bruges forskelligt.",
            wraplength=440,
        ).pack(anchor="w")

        ttk.Label(performance, text="Antal CPU-kerner", font=("", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        cpu_box = self._create_fixed_dropdown(
            performance,
            self._settings_vars["cpu_threads"],
            values=tuple(display_cpu_thread(value, self._language_code()) for value in CPU_THREAD_OPTIONS),
            width_px=176,
        )
        cpu_box.grid(row=1, column=0, sticky="w", pady=(0, 12))
        ttk.Label(
            performance,
            text="Bruges aktivt ved alle Assistent-søgemetoder. Auto bruger alle tilgængelige CPU-kerner minus 2. Ved 2 eller flere valgte kerner reserveres én kerne til UI og løbende resultater.",
            wraplength=440,
        ).grid(row=2, column=0, sticky="w")

        button_row = ttk.Frame(outer)
        button_row.pack(fill="x", pady=(12, 0))

        def save(close=False):
            language_label = self._settings_vars["language"].get()
            language_code = LANGUAGE_CODES_BY_LABEL.get(language_label, "da")
            self.app_settings.update({
                "language": language_code,
                "auto_template": bool(self._settings_vars["auto_template"].get()),
                "warn_overwrite": bool(self._settings_vars["warn_overwrite"].get()),
                "autosave": bool(self._settings_vars["autosave"].get()),
                "auto_backup": bool(self._settings_vars["auto_backup"].get()),
                "cpu_threads": internal_cpu_thread(self._settings_vars["cpu_threads"].get()) if internal_cpu_thread(self._settings_vars["cpu_threads"].get()) in CPU_THREAD_OPTIONS else "Auto",
            })
            if self._save_app_settings():
                old_flag = getattr(self, "_applying_app_settings", False)
                self._applying_app_settings = True
                try:
                    self._apply_language_to_current_ui()
                    self._schedule_workspace_autosave()
                    self._main_notice(self._tr("Indstillinger gemt."))
                finally:
                    self._applying_app_settings = old_flag
            if close:
                win.destroy()

        ttk.Button(button_row, text="Gem", command=lambda: save(False)).pack(side="right", padx=(6, 0))
        ttk.Button(button_row, text="Gem og luk", command=lambda: save(True)).pack(side="right", padx=(6, 0))
        ttk.Button(button_row, text="Annuller", command=win.destroy).pack(side="right")

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        try:
            win.update_idletasks()
            x = self.winfo_rootx() + max(0, (self.winfo_width() - win.winfo_width()) // 2)
            y = self.winfo_rooty() + max(0, (self.winfo_height() - win.winfo_height()) // 3)
            win.geometry(f"+{x}+{y}")
            self._translate_widget_tree(win)
            win.title(self._tr("Indstillinger"))
            win.deiconify()
            win.lift()
            win.focus_force()
        except Exception:
            try:
                win.deiconify()
            except Exception:
                pass


    # ---------- UI edit mode ----------

    def _init_ui_font_bases(self):
        self._ui_font_bases = {}
        for name in ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont", "TkHeadingFont", "TkTooltipFont"):
            try:
                font = tkfont.nametofont(name)
                self._ui_font_bases[name] = int(font.cget("size"))
            except Exception:
                pass

    def _apply_ui_scale(self):
        try:
            scale = max(0.75, min(1.40, float(getattr(self, "app_settings", {}).get("ui_scale", 1.0))))
        except Exception:
            scale = 1.0
        for name, base_size in getattr(self, "_ui_font_bases", {}).items():
            try:
                font = tkfont.nametofont(name)
                size = int(round(float(base_size) * scale))
                if base_size < 0:
                    size = -max(1, int(round(abs(float(base_size)) * scale)))
                elif size == 0:
                    size = 1
                font.configure(size=size)
            except Exception:
                pass
        try:
            self.option_add("*Font", tkfont.nametofont("TkDefaultFont"))
        except Exception:
            pass

    def _open_ui_edit_window(self):
        try:
            if self.ui_edit_window is not None and self.ui_edit_window.winfo_exists():
                self.ui_edit_window.lift()
                self.ui_edit_window.focus_force()
                return
        except Exception:
            self.ui_edit_window = None

        win = tk.Toplevel(self)
        self.ui_edit_window = win
        win.title(self._tr("UI-redigering"))
        win.geometry("520x330")
        win.minsize(500, 300)
        win.transient(self)

        outer = ttk.Frame(win, padding=14)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text=self._tr("UI-redigering"), font=("", 16, "bold")).pack(anchor="w", pady=(0, 10))

        enabled_var = tk.BooleanVar(value=bool(getattr(self, "ui_edit_mode", False)))
        ttk.Checkbutton(
            outer,
            text=self._tr("Aktivér redigeringstilstand"),
            variable=enabled_var,
            command=lambda: self._set_ui_edit_mode_enabled(bool(enabled_var.get())),
        ).pack(anchor="w", pady=(0, 8))

        ttk.Label(
            outer,
            text=self._tr("Dobbeltklik på en fast tekst i programmet for at omdøbe den."),
            wraplength=470,
            foreground="#555555",
        ).pack(anchor="w", pady=(0, 16))

        scale_row = ttk.Frame(outer)
        scale_row.pack(fill="x", pady=(0, 10))
        ttk.Label(scale_row, text=self._tr("Tekststørrelse")).pack(side="left")
        scale_label_var = tk.StringVar()
        try:
            start_percent = int(round(float(self.app_settings.get("ui_scale", 1.0)) * 100))
        except Exception:
            start_percent = 100
        scale_var = tk.IntVar(value=start_percent)

        def update_scale_label(*_):
            scale_label_var.set(f"{int(scale_var.get())}%")

        def apply_scale(_event=None):
            self.app_settings["ui_scale"] = max(75, min(140, int(scale_var.get()))) / 100.0
            self._apply_ui_scale()
            self._save_app_settings()
            update_scale_label()

        scale = ttk.Scale(outer, from_=85, to=125, variable=scale_var, command=lambda _v: apply_scale())
        scale.pack(fill="x", pady=(0, 2))
        update_scale_label()
        ttk.Label(outer, textvariable=scale_label_var).pack(anchor="e")

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(14, 0))

        def reset_scale():
            scale_var.set(100)
            apply_scale()

        def reset_texts():
            self.app_settings["ui_text_overrides"] = {}
            self._save_app_settings()
            self._apply_language_to_current_ui()
            self._main_notice(self._tr("Egne tekster nulstillet."))

        ttk.Button(buttons, text=self._tr("Nulstil størrelse"), command=reset_scale).pack(side="left")
        ttk.Button(buttons, text=self._tr("Nulstil egne tekster"), command=reset_texts).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text=self._tr("Luk"), command=win.destroy).pack(side="right")

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        try:
            win.update_idletasks()
            x = self.winfo_rootx() + max(0, (self.winfo_width() - win.winfo_width()) // 2)
            y = self.winfo_rooty() + max(0, (self.winfo_height() - win.winfo_height()) // 3)
            win.geometry(f"+{x}+{y}")
            win.lift()
            win.focus_force()
        except Exception:
            pass

    def _set_ui_edit_mode_enabled(self, enabled):
        enabled = bool(enabled)
        self.ui_edit_mode = enabled
        if enabled and not getattr(self, "_ui_edit_bind_id", None):
            try:
                self._ui_edit_bind_id = self.bind_all("<Double-Button-1>", self._ui_edit_mode_double_click, add="+")
            except Exception:
                self._ui_edit_bind_id = None
            self._main_notice("Redigeringstilstand aktiv")
        elif not enabled and getattr(self, "_ui_edit_bind_id", None):
            try:
                self.unbind_all("<Double-Button-1>")
            except Exception:
                pass
            self._ui_edit_bind_id = None
            self._main_notice("Redigeringstilstand slået fra")

    def _ui_edit_mode_double_click(self, event):
        if not getattr(self, "ui_edit_mode", False):
            return None
        widget = getattr(event, "widget", None)
        if widget is None:
            return None
        try:
            if isinstance(widget, ttk.Treeview):
                region = widget.identify_region(event.x, event.y)
                if region == "heading":
                    col = widget.identify_column(event.x)
                    columns = widget.cget("columns")
                    if col.startswith("#"):
                        index = int(col[1:]) - 1
                        if 0 <= index < len(columns):
                            column_name = columns[index]
                            current = widget.heading(column_name, "text")
                            key = getattr(self, "_tree_heading_text_keys", {}).get((str(widget), column_name), current)
                            self._open_text_edit_dialog(key, current, lambda new_text, col=column_name: widget.heading(col, text=new_text))
                            return "break"
        except Exception:
            pass

        try:
            current = widget.cget("text")
        except Exception:
            return None
        if not isinstance(current, str) or not current.strip():
            return None
        key = getattr(widget, "_vman_i18n_key", current)
        self._open_text_edit_dialog(key, current, lambda new_text, widget=widget: widget.configure(text=new_text))
        return "break"

    def _open_text_edit_dialog(self, source_key, current_text, apply_callback=None):
        win = tk.Toplevel(self)
        win.title(self._tr("Redigér tekst"))
        win.transient(self)
        win.resizable(False, False)
        outer = ttk.Frame(win, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=self._tr("Oprindelig tekst"), font=("", 10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(outer, text=str(current_text), wraplength=420).grid(row=1, column=0, sticky="w", pady=(2, 10))
        ttk.Label(outer, text=self._tr("Ny tekst"), font=("", 10, "bold")).grid(row=2, column=0, sticky="w")
        var = tk.StringVar(value=str(current_text))
        entry = ttk.Entry(outer, textvariable=var, width=52)
        entry.grid(row=3, column=0, sticky="ew", pady=(2, 12))
        buttons = ttk.Frame(outer)
        buttons.grid(row=4, column=0, sticky="e")

        def save_text():
            new_text = str(var.get()).strip()
            if not new_text:
                return
            overrides = dict(self.app_settings.get("ui_text_overrides", {}) or {})
            overrides[str(source_key)] = new_text
            self.app_settings["ui_text_overrides"] = overrides
            self._save_app_settings()
            try:
                if apply_callback is not None:
                    apply_callback(new_text)
            except Exception:
                pass
            self._apply_language_to_current_ui()
            win.destroy()

        ttk.Button(buttons, text=self._tr("Annuller"), command=win.destroy).pack(side="right")
        ttk.Button(buttons, text=self._tr("Gem tekst"), command=save_text).pack(side="right", padx=(0, 6))
        try:
            win.update_idletasks()
            x = self.winfo_rootx() + max(0, (self.winfo_width() - win.winfo_width()) // 2)
            y = self.winfo_rooty() + max(0, (self.winfo_height() - win.winfo_height()) // 3)
            win.geometry(f"+{x}+{y}")
            entry.focus_set()
            entry.selection_range(0, "end")
            win.grab_set()
        except Exception:
            pass

    def _confirm_overwrite_action(self, action_text, parent=None):
        if not self._setting_bool("warn_overwrite", False):
            return True
        try:
            return bool(messagebox.askyesno(
                "Bekræft handling",
                f"{action_text} kan overskrive nuværende data. Vil du fortsætte?",
                parent=parent or self,
            ))
        except Exception:
            return True

    def _workspace_payload(self):
        try:
            position = internal_position(self.position_var.get()) if hasattr(self, "position_var") else "Keepere"
            start_stats = {
                stat: var.get()
                for stat, var in getattr(self, "start_stat_vars", {}).items()
            }
            return {
                "format": "VMAN Training Planner autosave",
                "version": "3.00",
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "settings": copy.deepcopy(getattr(self, "app_settings", {})),
                "workspace": {
                    "position": position,
                    "start_age": self.start_age_var.get() if hasattr(self, "start_age_var") else "15.0",
                    "xp_at_hard_intensity": self.xp_var.get() if hasattr(self, "xp_var") else "180",
                    "xp_reference_intensity": internal_intensity(self.xp_reference_intensity_var.get()) if hasattr(self, "xp_reference_intensity_var") else "Hård",
                    "experience_bonus": {
                        "enabled": bool(self.ebr_enabled_var.get()) if hasattr(self, "ebr_enabled_var") else False,
                        "ratio": self.ebr_ratio_var.get() if hasattr(self, "ebr_ratio_var") else "1.10",
                        "knee": self.ebr_knee_var.get() if hasattr(self, "ebr_knee_var") else "linear",
                    },
                    "player_link_raw": getattr(self, "_player_link_raw_value", ""),
                    "player_link_display": getattr(self, "_player_link_display_value", ""),
                    "start_stats": start_stats,
                    "program": [
                        self._serialize_program_item(item)
                        for item in getattr(self, "program", [])
                    ],
                },
            }
        except Exception:
            return None

    def _schedule_workspace_autosave(self):
        if not getattr(self, "_settings_runtime_ready", False):
            return
        if not self._setting_bool("autosave", True) and not self._setting_bool("auto_backup", True):
            return
        # 4.51: Auto-save er fil-I/O og må ikke konkurrere med løbende UI-input.
        # Én pending autosave er nok; den får længere forsinkelse.
        if self._autosave_after_id is not None:
            return
        try:
            self._autosave_after_id = self.after(2500, self._write_workspace_autosave)
        except Exception:
            pass

    def _write_workspace_autosave(self):
        self._autosave_after_id = None
        payload = self._workspace_payload()
        if not payload:
            return
        try:
            base = self._settings_dir()
            if self._setting_bool("autosave", True):
                autosave_dir = base / "autosave"
                autosave_dir.mkdir(parents=True, exist_ok=True)
                with open(autosave_dir / "current_workspace.json", "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)

            if self._setting_bool("auto_backup", True):
                backup_dir = base / "backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%d_%H%M%S")
                with open(backup_dir / f"backup_{stamp}.json", "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)

                backups = sorted(backup_dir.glob("backup_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                for old in backups[40:]:
                    try:
                        old.unlink()
                    except Exception:
                        pass
        except Exception:
            pass

    def _show_main_menu(self, event=None):
        if not hasattr(self, "main_menu"):
            return
        trigger = getattr(self, "main_menu_trigger", None)
        if trigger is None:
            return
        try:
            x = trigger.winfo_rootx()
            y = trigger.winfo_rooty() + trigger.winfo_height() + 2
            self.main_menu.tk_popup(x, y)
        finally:
            try:
                self.main_menu.grab_release()
            except Exception:
                pass

    def _position_main_menu_trigger(self, _event=None):
        trigger = getattr(self, "main_menu_trigger", None)
        if trigger is None:
            return
        try:
            # 4.12: Sørg for at både topbjælken og selve hamburgeren ligger i
            # yderste lag over alt andet indhold.
            header_bar = getattr(self, "header_bar", None)
            if header_bar is not None:
                try:
                    header_bar.lift()
                except Exception:
                    pass
            try:
                trigger.lift()
            except Exception:
                pass
            try:
                trigger.tk.call("raise", trigger._w)
            except Exception:
                pass
        except Exception:
            pass

    def _build_program_list(self, parent):
        frame = ttk.LabelFrame(parent, text="", padding=10, style="MainSection.TLabelframe")
        self.program_list_frame = frame
        frame.pack(fill="x", expand=False, pady=(0, 33))

        topbar = ttk.Frame(frame)
        topbar.pack(fill="x", pady=(6, 8))

        history_controls = ttk.Frame(topbar)
        history_controls.pack(side="left", padx=(0, 0), pady=0)

        # 3.75: Fast sort afgrænsning omkring skabelonvælgeren. Den
        # erstatter den Windows-orange fokusmarkering som visuel ramme.
        self.template_selector_border = tk.Frame(history_controls, bg="#000000", bd=0)
        self.presets_button = ttk.Menubutton(
            self.template_selector_border,
            textvariable=self.selected_template_name_var,
            width=13,
            takefocus=0,
            style="TemplateDropdown.TMenubutton",
        )
        self.presets_menu = tk.Menu(self.presets_button, tearoff=0)
        try:
            self.presets_menu.configure(postcommand=self._refresh_presets_menu)
        except Exception:
            pass
        self._refresh_presets_menu()
        self.presets_button["menu"] = self.presets_menu

        self.template_selector_border.grid(row=0, column=0, sticky="new", pady=0)
        self.presets_button.pack(fill="x", expand=False, padx=1, pady=1)
        self.undo_button = ttk.Button(history_controls, text="↺", width=3, command=self._undo_main, state="disabled")
        self.undo_button.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 3))
        self.redo_button = ttk.Button(history_controls, text="↻", width=3, command=self._redo_main, state="disabled")
        self.redo_button.grid(row=0, column=2, sticky="nsew", padx=(4, 0), pady=(0, 3))

        # 1.77: Almindelig standardknap som i 1.71-linjen.
        self.assistant_button = ttk.Button(
            topbar,
            text="Assistent",
            command=self._open_assistant,
        )
        self.assistant_button.pack(side="right", padx=(10, 0))

        # 3.78: Hamburger-knappen er flyttet til den faste topbjælke, så
        # Træningsprogram-panelet kun indeholder træningsrelaterede kontroller.
        self.main_menu = tk.Menu(self, tearoff=0)
        for label in ("Indstillinger", "Hjælp", "Donation", "Om VMAN Training Planner"):
            self.main_menu.add_command(label=self._tr(label), command=lambda name=label: self._handle_main_menu_action(name))

        menu_parent = getattr(self, "header_bar", frame)
        self.main_menu_trigger = tk.Canvas(
            menu_parent,
            width=38,
            height=26,
            bg=(getattr(self, "_theme_panel_bg", None) or ttk.Style(self).lookup("TFrame", "background") or "#2f2b2b"),
            bd=0,
            highlightthickness=0,
            cursor="arrow",
        )
        for y in (5, 13, 21):
            self.main_menu_trigger.create_line(4, y, 34, y, fill=(getattr(self, "_theme_panel_fg", "#f0f0f0")), width=3, capstyle=tk.ROUND)
        self.main_menu_trigger.bind("<Button-1>", self._show_main_menu)
        self.main_menu_trigger.pack(side="right", padx=(10, 0), pady=(1, 0))
        self.header_bar.bind("<Configure>", self._position_main_menu_trigger, add="+")
        self.content_frame.bind("<Configure>", self._position_main_menu_trigger, add="+")
        self.bind("<Configure>", self._position_main_menu_trigger, add="+")
        self.after_idle(self._position_main_menu_trigger)

        # 2.21: Kompakt, vandret Erfaringsbonus-række placeret lige til
        # venstre for Assistent-knappen. Ratio og Gradient ligger på samme linje.
        ebr_row_frame = ttk.Frame(topbar)
        ebr_row_frame.pack(side="right", padx=(0, 0), pady=0)

        self.ebr_check = ttk.Checkbutton(
            ebr_row_frame,
            text="Erfaringsbonus",
            variable=self.ebr_enabled_var,
            command=self._on_ebr_toggle,
        )
        self.ebr_check.pack(side="left")

        ttk.Label(ebr_row_frame, text="Ratio").pack(side="left", padx=(10, 4))
        self.ebr_ratio_spinbox = tk.Spinbox(
            ebr_row_frame,
            from_=1.01,
            to=EBR_RATIO_MAX,
            increment=0.01,
            format="%.2f",
            textvariable=self.ebr_ratio_var,
            width=4,
            state="disabled",
            command=self._settings_changed,
        )
        self.ebr_ratio_spinbox.pack(side="left")
        self._configure_numeric_widget(self.ebr_ratio_spinbox, decimals=2, max_value=EBR_RATIO_MAX)

        ttk.Label(ebr_row_frame, text="Gradient").pack(side="left", padx=(14, 4))
        self.ebr_knee_frame = ttk.Frame(ebr_row_frame)
        self.ebr_knee_frame.pack(side="left")

        for value in ("early", "linear", "late"):
            canvas = tk.Canvas(
                self.ebr_knee_frame,
                width=29,
                height=19,
                highlightthickness=1,
                highlightbackground=(getattr(self, "_theme_panel_border", "#474242")),
                background=(getattr(self, "_theme_panel_bg", "#2f2b2b")),
                cursor="arrow",
            )
            canvas.pack(side="left", padx=(0, 8))
            canvas.bind("<Button-1>", lambda event, v=value: self._select_ebr_knee(v))
            self.ebr_knee_canvases.append((value, canvas))

        self._redraw_ebr_knee_icons()

        columns = ("delete", "days", "exercise", "intensity", "distribution")
        self.program_tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            height=6,
            selectmode="extended",
            style="Program.Treeview",
        )
        try:
            self.program_tree.tag_configure("darkrow", background=getattr(self, "_theme_panel_bg", "#2f2b2b"), foreground=getattr(self, "_theme_panel_fg", "#f0f0f0"))
        except Exception:
            pass
        for _col, _label in (("delete", "🗑"), ("days", "Dage"), ("exercise", "Øvelse"), ("intensity", "Intensitet"), ("distribution", "Sessions")):
            self._tree_heading_text_keys[(str(self.program_tree), _col)] = _label
            self.program_tree.heading(_col, text=self._tr(_label))
        self.program_tree.column("delete", width=42, minwidth=42, anchor="center", stretch=False)
        self.program_tree.column("days", width=60, minwidth=60, anchor="center", stretch=False)
        self.program_tree.column("exercise", width=175, minwidth=140, stretch=False)
        self.program_tree.column("intensity", width=95, minwidth=80, stretch=False)
        # 3.17: Samme kolonneregel som i Assistent-søgningen: alle faste
        # kolonner forbliver uændrede, og kun Sessions/Program-delen optager
        # resten. Ingen ekstra tom kolonne til højre.
        self.program_tree.column("distribution", width=475, minwidth=260, stretch=True)
        self.program_tree.pack(fill="both", expand=True, pady=(2, 0))
        self.program_tree_header_borders = []
        self.program_tree.bind("<Configure>", self._resize_program_tree_columns, add="+")
        self.program_tree.bind("<Configure>", self._update_program_tree_column_separators, add="+")
        self.after_idle(lambda: self._resize_program_tree_columns(force=True))
        self.after_idle(self._update_program_tree_column_separators)
        self.program_drop_indicator = tk.Frame(self.program_tree, height=2, bg="#2d8cff")
        self.program_drop_indicator.place_forget()
        self._pending_program_trash_row = None
        self._pending_program_trash_column = None
        self._program_column_resize_blocked = False

        self.program_tree.bind("<Double-1>", self._on_program_double_click)
        self.program_tree.bind("<<TreeviewSelect>>", lambda event: self._update_quantity_editor_from_selection())
        self.program_tree.bind("<Button-1>", self._on_program_button_press)
        self.program_tree.bind("<Command-Button-1>", self._on_program_native_multi_select_click)
        self.program_tree.bind("<Control-Button-1>", self._on_program_native_multi_select_click)
        self.program_tree.bind("<Shift-Button-1>", self._on_program_native_multi_select_click)
        self.program_tree.bind("<B1-Motion>", self._on_program_drag_motion)
        self.program_tree.bind("<ButtonRelease-1>", self._on_program_button_release)
        self.program_tree.bind("<ButtonRelease-1>", self._on_program_trash_release_debug_fallback, add="+")
        self.program_tree.bind("<Button-3>", self._show_program_context_menu)
        self.program_tree.bind("<Button-2>", self._show_program_context_menu)

        self._build_program_context_menu()

        self.program_tree.bind("<Delete>", lambda event: self._remove_selected_phase())
        self.program_tree.bind("<BackSpace>", lambda event: self._remove_selected_phase())
        # Program-shortcuts bindes kun til tabellen, ikke globalt.
        self.program_tree.bind("<Command-c>", self._handle_global_copy)
        self.program_tree.bind("<Control-c>", self._handle_global_copy)
        self.program_tree.bind("<Command-v>", self._handle_global_paste)
        self.program_tree.bind("<Control-v>", self._handle_global_paste)
        self.program_tree.bind("<Command-d>", self._handle_global_duplicate)
        self.program_tree.bind("<Control-d>", self._handle_global_duplicate)

        btns = ttk.Frame(frame)
        btns.pack(fill="x", pady=(8, 0))

        self.quantity_label = ttk.Label(btns, text="")

        self.quantity_var = tk.IntVar(value=1)
        self.quantity_spinbox = tk.Spinbox(
            btns,
            from_=1,
            to=99999,
            textvariable=self.quantity_var,
            width=7,
            state="disabled",
            command=self._apply_quantity_editor,
        )
        self._configure_numeric_widget(self.quantity_spinbox, integer=True, max_value=99999)
        # 2.27: Antal-feltet vises først, når præcis én øvelse/blok er valgt.
        self.quantity_label_visible = False
        self.quantity_spinbox.bind("<KeyRelease>", lambda event: self._schedule_quantity_editor_apply())
        self.quantity_spinbox.bind("<Return>", lambda event: self._apply_quantity_editor())
        self.quantity_spinbox.bind("<KP_Enter>", lambda event: self._apply_quantity_editor())

        self.export_menu = tk.Menu(self, tearoff=0)
        self.export_menu.add_command(label=self._tr("Historik som CSV"), command=self._export_history_csv)
        self.export_menu.add_command(label=self._tr("Rapport som PDF"), command=self._export_report_pdf)
        self.export_menu.add_command(label=self._tr("Rapport som DOC"), command=self._export_report_doc)

        result_frame = ttk.LabelFrame(parent, text="", padding=(10, 5, 10, 4), style="MainSection.TLabelframe")
        self.result_section_frame = result_frame
        # 2.24: Resultat har fast synkroniseret højde, så det må ikke
        # udvide sig til al resterende plads i højre side.
        result_frame.pack(fill="x", expand=False, pady=(0, 0))

        result_topbar = ttk.Frame(result_frame)
        result_topbar.pack(fill="x", pady=(3, 0))

        # 2.22: Graf- og eksportknapperne hører nu til Resultat-afdelingen
        # i stedet for Træningsprogram-afdelingens nederste knaplinje.
        self.export_button = ttk.Button(result_topbar, text="Eksportér", command=self._show_export_menu)
        self.export_button.pack(side="right", padx=(0, 0))

        self.graph_button = ttk.Button(result_topbar, text="Vis graf", command=self._show_rating_graph)
        self.graph_button.pack(side="right", padx=(0, 6))

        self.group_report_button = ttk.Button(result_topbar, text="Grupperapport", command=self._show_group_report_window)
        self.group_report_button.pack(side="right", padx=(0, 6), before=self.graph_button)
        self.group_report_button.pack_forget()

        self.total_days_label = ttk.Label(
            result_topbar,
            text="Samlet: 0 dage — Vurderingstal: - — Gennemsnit: - — Alder: 15,00 år",
        )
        self.total_days_label.pack(side="left", anchor="w")

        self._build_live_stats_panel(result_frame)

    def _build_live_stats_panel(self, parent):
        self.live_stats_frame = ttk.Frame(parent)
        self.live_stats_frame.pack(fill="x", pady=(10, 0))

        # 4.66 Windows: Udvid den hvide resultatflade, så dens bund flugter
        # med bunden af Opdater-knappen i Session-sektionen. Mac beholdes uændret.
        live_canvas_height = 298 if self._is_windows_ui() else 265
        self.live_stats_canvas = tk.Canvas(
            self.live_stats_frame,
            height=live_canvas_height,
            highlightthickness=1,
            highlightbackground="black",
            highlightcolor="black",
            background="white",
        )
        self.live_stats_canvas.pack(fill="x", expand=False)
        self.live_stats_canvas.bind("<Configure>", lambda event: self._redraw_live_stats_panel())


    def _status_color_for_stat(self, value, average):
        if average <= 0:
            return "#2f7d1a"

        ratio = value / average
        if ratio > 1.15:
            return "#d62f00"
        if ratio > 1.10:
            return "#d8aa00"
        return "#2f7d1a"

    def _update_start_rating_label(self):
        if not hasattr(self, "start_rating_var"):
            return
        try:
            summary, position = self._current_start_summary()
            rating = float(summary.get(f"Vurderingstal_{position}", 0.0))
            self.start_rating_var.set(f"{rating:.2f}")

            if hasattr(self, "start_stat_sum_var"):
                stats = POSITION_STATS[position]
                total = sum(float(summary.get(stat, 0.0)) for stat in stats)
                if abs(total - round(total)) < 0.005:
                    self.start_stat_sum_var.set(str(int(round(total))))
                else:
                    self.start_stat_sum_var.set(f"{total:.2f}")
        except Exception:
            self.start_rating_var.set("-")
            if hasattr(self, "start_stat_sum_var"):
                self.start_stat_sum_var.set("-")

    def _current_start_summary(self):
        position = internal_position(self.position_var.get())
        start_stats = self._get_start_stats()
        state = PlayerState(stats=start_stats, stat_names=POSITION_STATS[position])
        return summarize_state(state, position=position), position

    def _update_live_stats_panel(self, summary=None, position=None):
        if not hasattr(self, "live_stats_canvas"):
            return

        try:
            if summary is None or position is None:
                summary, position = self._current_start_summary()
        except Exception:
            self.live_stats_summary = None
            self.live_stats_position = None
            self._redraw_live_stats_panel()
            return

        self.live_stats_summary = summary
        self.live_stats_position = internal_position(position)
        self._redraw_live_stats_panel()

    def _rounded_rating_for_display(self, rating):
        rating = max(0.0, min(100.0, float(rating)))
        return max(0, min(100, int(rating + 0.5)))

    def _rating_badge_colors(self, rating):
        display_rating = self._rounded_rating_for_display(rating)

        if display_rating >= 100:
            return "#1a1a1a", "#000000"

        bucket = display_rating // 10

        colors = {
            0: ("#45ad34", "#256c1d"),   # grøn
            1: ("#bfd000", "#6c8400"),   # lime/grøn-gul
            2: ("#ffcd16", "#a86d00"),   # gul
            3: ("#f28a00", "#b76200"),   # orange
            4: ("#df4828", "#8f2b18"),   # rød
            5: ("#e24ab3", "#84247a"),   # pink/magenta
            6: ("#b35ad0", "#74288c"),   # lilla
            7: ("#4a86df", "#2e459e"),   # blå
            8: ("#25a9cf", "#076b86"),   # turkis/cyan
            9: ("#5f6060", "#242526"),   # grå/sort
        }

        return colors.get(bucket, ("#5f6060", "#242526"))


    def _draw_rating_badge_antialiased(self, canvas, cx, cy, radius, fill, outline, rating_text):
        """Draw the result rating badge in high resolution to avoid jagged/pixelated edges."""
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageTk

            scale = 4
            pad = 8
            size = int((radius * 2 + pad * 2) * scale)
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            bbox = (
                int(pad * scale),
                int(pad * scale),
                int((pad + radius * 2) * scale),
                int((pad + radius * 2) * scale),
            )
            draw.ellipse(bbox, fill=fill, outline=outline, width=max(1, int(4 * scale)))

            font = None
            for candidate in ("arial.ttf", "DejaVuSans-Bold.ttf"):
                try:
                    font = ImageFont.truetype(candidate, int(30 * scale))
                    break
                except Exception:
                    pass
            if font is None:
                font = ImageFont.load_default()

            text = str(rating_text)
            try:
                tb = draw.textbbox((0, 0), text, font=font)
                tw, th = tb[2] - tb[0], tb[3] - tb[1]
            except Exception:
                tw, th = draw.textlength(text, font=font), int(30 * scale)

            if 'tb' in locals():
                tx = (size - tw) / 2 - tb[0]
                ty = (size - th) / 2 - tb[1]
            else:
                tx = (size - tw) / 2
                ty = (size - th) / 2
            shadow_offset = int(1.5 * scale)
            draw.text((tx + shadow_offset, ty + shadow_offset), text, font=font, fill=outline)
            draw.text((tx, ty), text, font=font, fill="#ffffff")

            img = img.resize((size // scale, size // scale), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._live_stats_badge_images = [photo]
            canvas.create_image(cx, cy, image=photo, anchor="center")
            return True
        except Exception:
            return False

    def _redraw_live_stats_panel(self):
        if not hasattr(self, "live_stats_canvas"):
            return

        canvas = self.live_stats_canvas
        canvas.delete("all")

        actual_width = max(canvas.winfo_width(), 720)
        summary = getattr(self, "live_stats_summary", None)
        position = getattr(self, "live_stats_position", None)

        if not summary or not position:
            desired_height = 298 if self._is_windows_ui() else 265
            if int(canvas.cget("height")) != desired_height:
                canvas.configure(height=desired_height)
            width = actual_width
            height = desired_height
            canvas.create_rectangle(0, 0, width, height, fill="white", outline="")
            canvas.create_text(
                12,
                height // 2,
                text=self._tr("Ingen stats endnu"),
                anchor="w",
                fill="#666666",
                font=("", 12),
            )
            return

        stats = POSITION_STATS[position]
        average = float(summary.get("Gennemsnit", 0.0))
        rating = float(summary.get(f"Vurderingstal_{position}", 0.0))
        total = sum(float(summary.get(stat, 0.0)) for stat in stats)

        # Fixed overall geometry
        left_margin = 14
        right_margin = 12
        top = 34
        row_h = 29
        col_gap = 16
        section_gap = 24

        # Reserve a guaranteed non-overlapping area on the right.
        right_block_w = 190
        right_block_x = actual_width - right_margin - right_block_w
        left_area_left = left_margin
        left_area_right = right_block_x - section_gap
        left_area_w = max(430, left_area_right - left_area_left)

        # Two stat columns inside the left area.
        col_w = (left_area_w - col_gap) / 2

        # Adapt label/value/bar widths to avoid overlap as the window narrows.
        # 3.85: Lange statnavne som "Dødboldsituationer" skal have nok labelplads,
        # så teksten ikke bliver spist af baren.
        try:
            label_font = tkfont.Font(font=("", 11))
            measured_label_w = max(label_font.measure(self._display_stat(stat)) for stat in stats) + 18
        except Exception:
            measured_label_w = 130
        if actual_width < 950:
            label_w = min(132, max(92, int(col_w * 0.42), measured_label_w))
        else:
            label_w = min(155, max(118, measured_label_w))
        value_w = 50 if actual_width < 950 else 58
        value_offset = 6 if actual_width < 950 else 8
        bar_w = max(50, col_w - label_w - value_w - 18)

        width = actual_width
        height = 298 if self._is_windows_ui() else 265
        if int(canvas.cget("height")) != height:
            canvas.configure(height=height)

        canvas.create_rectangle(0, 0, width, height, fill="white", outline="")
        # 1.70: fjernet den grønne top-linje under Resultat.

        # Left stats area
        for idx, stat in enumerate(stats):
            col = idx // 5
            row = idx % 5

            x = left_area_left + col * (col_w + col_gap)
            y = top + row * row_h

            value = float(summary.get(stat, 0.0))
            color = self._status_color_for_stat(value, average)

            canvas.create_text(
                x,
                y + 1,
                text=self._display_stat(stat),
                anchor="nw",
                fill="#333333",
                font=("", 11),
            )

            bar_x = x + label_w
            bar_y = y + 4
            bar_h = 9

            canvas.create_rectangle(
                bar_x,
                bar_y,
                bar_x + bar_w,
                bar_y + bar_h,
                fill="#d7d7d7",
                outline="#c0c0c0",
            )

            progress_value = max(0.0, min(value, 100.0))
            if progress_value >= 100.0:
                progress_fraction = 1.0
            else:
                progress_fraction = progress_value - int(progress_value)
                if progress_fraction < 0:
                    progress_fraction = 0.0
            fill_w = bar_w * progress_fraction
            canvas.create_rectangle(
                bar_x,
                bar_y,
                bar_x + fill_w,
                bar_y + bar_h,
                fill=color,
                outline="",
            )

            canvas.create_text(
                bar_x + bar_w + value_offset,
                y + 8,
                text=f"{value:.1f}",
                anchor="w",
                fill=color,
                font=("", 15 if actual_width < 950 else 16, "bold"),
            )

        bottom_y = top + 5 * row_h + 10
        canvas.create_line(left_area_left, bottom_y - 7, left_area_left + left_area_w, bottom_y - 7, fill="#dddddd")
        canvas.create_text(
            left_area_left,
            bottom_y + 8,
            text=f"Total: {total:.1f}",
            anchor="w",
            fill="#333333",
            font=("", 13, "bold"),
        )

        # Right rating/legend area - always isolated from left area
        display_rating = self._rounded_rating_for_display(rating)
        badge_fill, badge_outline = self._rating_badge_colors(rating)

        badge_r = 43
        badge_cx = right_block_x + right_block_w // 2
        badge_cy = 72

        if not self._draw_rating_badge_antialiased(
            canvas,
            badge_cx,
            badge_cy,
            badge_r,
            badge_fill,
            badge_outline,
            str(display_rating),
        ):
            canvas.create_oval(
                badge_cx - badge_r,
                badge_cy - badge_r,
                badge_cx + badge_r,
                badge_cy + badge_r,
                fill=badge_fill,
                outline=badge_outline,
                width=4,
            )
            canvas.create_text(
                badge_cx,
                badge_cy,
                text=str(display_rating),
                anchor="center",
                fill=badge_outline,
                font=("", 26, "bold"),
            )
            canvas.create_text(
                badge_cx,
                badge_cy,
                text=str(display_rating),
                anchor="center",
                fill="white",
                font=("", 26, "bold"),
            )
        canvas.create_text(
            badge_cx,
            badge_cy + badge_r + 18,
            text=self._tr("Vurdering"),
            anchor="center",
            fill="#333333",
            font=("", 12),
        )

        legend_x = right_block_x + 8
        legend_y = 158

        canvas.create_text(
            legend_x,
            legend_y,
            text="> 115% of average" if self._language_code() == "en" else "> 115% af gennemsnit",
            anchor="w",
            fill="#d62f00",
            font=("", 11),
        )
        canvas.create_text(
            legend_x,
            legend_y + 25,
            text="> 110% of average" if self._language_code() == "en" else "> 110% af gennemsnit",
            anchor="w",
            fill="#d8aa00",
            font=("", 11),
        )
        canvas.create_text(
            legend_x,
            legend_y + 50,
            text="< 110% of average" if self._language_code() == "en" else "≤ 110% af gennemsnit",
            anchor="w",
            fill="#2f7d1a",
            font=("", 11),
        )

    # ---------- Assistent wizard ----------

    def _assistant_store_recall_snapshot(self):
        """Store latest Assistant search as serializable data for reliable recall."""
        try:
            data = copy.deepcopy(getattr(self, "assistant_data", {}) or {})
            pool = self._assistant_all_results()
            if data and (pool or data.get("_search_has_run")):
                data["search_results_pool"] = [self._assistant_result_to_dict(result) for result in pool]
                data["top_results"] = [self._assistant_result_to_dict(result) for result in pool[:10]]
                data["_search_has_run"] = bool(pool or data.get("_search_has_run"))
                data["_assistant_workspace_locked"] = True
                data["_assistant_reopen_step"] = 2
                self._assistant_recall_snapshot = data
                self._assistant_reset_warning_shown = False
        except Exception:
            pass

    def _assistant_has_search_state(self):
        try:
            data = getattr(self, "assistant_data", {}) or {}
            if data.get("_search_has_run") or data.get("top_results") or data.get("search_results_pool"):
                return True
            snapshot = getattr(self, "_assistant_recall_snapshot", None) or {}
            if snapshot.get("_search_has_run") or snapshot.get("top_results") or snapshot.get("search_results_pool"):
                return True
            try:
                if self._assistant_all_results():
                    return True
            except Exception:
                pass
        except Exception:
            pass
        return False

    def _ask_danish_yes_no(self, title, message, parent=None, default=False):
        """Vis en enkel Ja/Nej-dialog med egne knaptekster.

        macOS/Tk kan vise standard-messagebox med engelske knapper, selv når
        resten af UI'et er dansk. Derfor bruges denne dialog til VMANs egne
        bekræftelser, hvor knapperne skal følge appens sprog.
        """
        parent = parent or self
        result = {"value": bool(default)}
        try:
            win = tk.Toplevel(parent)
            win.title(self._tr(title))
            win.transient(parent)
            win.resizable(False, False)
            body = ttk.Frame(win, padding=14)
            body.pack(fill="both", expand=True)
            ttk.Label(body, text=self._tr(title), font=("", 12, "bold")).pack(anchor="w", pady=(0, 8))
            ttk.Label(body, text=self._tr(message), wraplength=440, justify="left").pack(anchor="w", fill="x")
            buttons = ttk.Frame(body)
            buttons.pack(fill="x", pady=(14, 0))

            def choose(value):
                result["value"] = bool(value)
                try:
                    win.grab_release()
                except Exception:
                    pass
                win.destroy()

            ttk.Button(buttons, text=self._tr("Nej"), command=lambda: choose(False), width=8).pack(side="right")
            ttk.Button(buttons, text=self._tr("Ja"), command=lambda: choose(True), width=8).pack(side="right", padx=(0, 6))
            win.protocol("WM_DELETE_WINDOW", lambda: choose(False))
            win.bind("<Escape>", lambda _event: choose(False))
            win.bind("<Return>", lambda _event: choose(True))
            try:
                win.update_idletasks()
                root = parent if parent is not None and parent.winfo_exists() else self
                x = root.winfo_rootx() + max(0, (root.winfo_width() - win.winfo_width()) // 2)
                y = root.winfo_rooty() + max(0, (root.winfo_height() - win.winfo_height()) // 3)
                win.geometry(f"+{x}+{y}")
            except Exception:
                pass
            win.grab_set()
            win.focus_force()
            parent.wait_window(win)
            return bool(result["value"])
        except Exception:
            return bool(default)

    def _assistant_clear_search_state_before_main_reset(self):
        try:
            self._assistant_recall_snapshot = None
            data = getattr(self, "assistant_data", {}) or {}
            data["search_results_pool"] = []
            data["top_results"] = []
            data["_search_has_run"] = False
            data["_assistant_workspace_locked"] = False
            data["_assistant_reopen_step"] = 0
            self.assistant_data = data
            if hasattr(self, "assistant_top_tree"):
                self._assistant_fill_top_results([])
        except Exception:
            pass

    def _assistant_confirm_search_reset_from_main(self):
        if not self._assistant_has_search_state():
            return True
        if getattr(self, "_assistant_reset_prompt_active", False) or getattr(self, "_assistant_reset_prompt_cooldown", False):
            return False
        self._assistant_reset_prompt_active = True
        try:
            parent = self.assistant_window if self.assistant_window is not None and self.assistant_window.winfo_exists() else self
            answer = bool(self._ask_danish_yes_no(
                "Ændringen vil rydde Assistent-søgningen",
                "Hvis du ændrer data i Spiller-sektionen, ryddes den aktuelle Assistent-søgning, fordi den ikke længere passer til spillerdataene.\n\nVil du fortsætte?",
                parent=parent,
                default=False,
            ))
            if answer:
                # Ryd med det samme, så flere samtidige trace-events fra samme
                # brugerhandling ikke viser den samme advarsel en ekstra gang.
                self._assistant_clear_search_state_before_main_reset()
            return answer
        finally:
            self._assistant_reset_prompt_active = False
            self._assistant_reset_prompt_cooldown = True
            try:
                self.after(250, lambda: setattr(self, "_assistant_reset_prompt_cooldown", False))
            except Exception:
                self._assistant_reset_prompt_cooldown = False

    def _assistant_reset_from_main_window(self, reason=""):
        if getattr(self, "_assistant_resetting_from_main", False):
            return
        try:
            self._assistant_resetting_from_main = True
            self._assistant_recall_snapshot = None
            self._assistant_stop_simulator_search()
            self.assistant_data = self._assistant_snapshot_from_ui()
            self.assistant_step = 0
            self.assistant_data["_assistant_workspace_locked"] = False
            self.assistant_data["_assistant_reopen_step"] = 0
            self.assistant_data["_search_has_run"] = False
            self.assistant_data["search_results_pool"] = []
            self.assistant_data["top_results"] = []
            if self.assistant_window is not None and self.assistant_window.winfo_exists():
                self._render_assistant_step()
                if hasattr(self, "assistant_start_status_var") and reason:
                    self.assistant_start_status_var.set(self._tr(reason))
        except Exception:
            pass
        finally:
            self._assistant_resetting_from_main = False

    def _assistant_restore_recall_data(self, snapshot):
        data = copy.deepcopy(snapshot or {})
        for key in ("search_results_pool", "top_results"):
            restored = []
            for item in list(data.get(key) or []):
                try:
                    restored.append(self._assistant_result_from_dict(item) if isinstance(item, dict) else item)
                except Exception:
                    pass
            data[key] = restored
        data["_search_has_run"] = bool(data.get("search_results_pool") or data.get("top_results") or data.get("_search_has_run"))
        return data

    def _assistant_recall_latest_search(self):
        # Do not rebuild the Assistant synchronously from the button command.
        # On macOS/Tk this could destroy the clicked button while Tk was still
        # finishing the command and produce "invalid command name ...button".
        try:
            self.after(120, self._assistant_recall_latest_search_now)
        except Exception:
            self._assistant_recall_latest_search_now()
        return "break"

    def _assistant_recall_latest_search_now(self):
        snapshot = copy.deepcopy(getattr(self, "_assistant_recall_snapshot", None) or {})
        if not snapshot:
            return
        try:
            self._assistant_stop_simulator_search()
            self.assistant_data = self._assistant_restore_recall_data(snapshot)
            has_results = bool(self.assistant_data.get("top_results") or self.assistant_data.get("search_results_pool") or self.assistant_data.get("_search_has_run"))
            self.assistant_step = 2 if has_results else max(0, min(2, int(self.assistant_data.get("_assistant_reopen_step", 0) or 0)))
            self.assistant_data["_assistant_workspace_locked"] = True
            self.assistant_data["_assistant_reopen_step"] = self.assistant_step

            # Recreate the Assistant window instead of only clearing/rebuilding its
            # body. This avoids dangling Tk button commands and also guarantees that
            # the restored result table is built from the restored snapshot.
            old_window = getattr(self, "assistant_window", None)
            if old_window is not None and old_window.winfo_exists():
                try:
                    old_window.destroy()
                except Exception:
                    pass
            self.assistant_window = None
            self._open_assistant()

            results = self._assistant_all_results()
            if hasattr(self, "assistant_top_tree"):
                self._assistant_fill_top_results(results)
                children = self.assistant_top_tree.get_children()
                if children:
                    self.assistant_top_tree.selection_set(children[0])
                    self.assistant_top_tree.focus(children[0])
            if hasattr(self, "assistant_search_status_var"):
                self.assistant_search_status_var.set(self._tr("Seneste søgning genkaldt."))
            elif hasattr(self, "assistant_start_status_var"):
                self.assistant_start_status_var.set(self._tr("Seneste søgning genkaldt."))
            if getattr(self, "active_group", None) and results:
                self._schedule_group_report_recompute(delay=120)
        except Exception as error:
            messagebox.showerror(self._tr("Assistent-fejl"), f"Genkald kunne ikke indlæses.\n\n{error}", parent=self.assistant_window if self.assistant_window is not None else self)

    def _save_assistant_recall_debug(self):
        try:
            initialfile = f"vman_assistant_recall_debug_{time.strftime('%Y%m%d_%H%M%S')}.txt"
            path = filedialog.asksaveasfilename(
                title=self._group_text("Gem genkald-debug", "Save recall debug"),
                initialfile=initialfile,
                defaultextension=".txt",
                filetypes=[("Text", "*.txt"), ("All files", "*.*")],
                parent=self.assistant_window if self.assistant_window is not None else self,
            )
            if not path:
                return
            snapshot = copy.deepcopy(getattr(self, "_assistant_recall_snapshot", None) or {})
            current = copy.deepcopy(getattr(self, "assistant_data", {}) or {})
            def info(data):
                return {
                    "keys": sorted(list((data or {}).keys())),
                    "search_results_pool_len": len((data or {}).get("search_results_pool") or []),
                    "top_results_len": len((data or {}).get("top_results") or []),
                    "search_has_run": (data or {}).get("_search_has_run"),
                    "reopen_step": (data or {}).get("_assistant_reopen_step"),
                    "position": (data or {}).get("position"),
                    "xp": (data or {}).get("xp"),
                }
            lines = [
                "VMAN Training Planner Assistent genkald-debug",
                "====================================",
                f"Tid: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                "",
                "Snapshot-info:",
                json.dumps(info(snapshot), ensure_ascii=False, indent=2, default=str),
                "",
                "Aktuel assistent-data-info:",
                json.dumps(info(current), ensure_ascii=False, indent=2, default=str),
                "",
                "Snapshot rådata:",
                json.dumps(snapshot, ensure_ascii=False, indent=2, default=str),
            ]
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            if hasattr(self, "assistant_start_status_var"):
                self.assistant_start_status_var.set(f"Genkald-debug gemt: {path}")
        except Exception as error:
            messagebox.showerror(self._tr("Assistent-fejl"), str(error), parent=self.assistant_window if self.assistant_window is not None else self)

    def _assistant_snapshot_from_ui(self):
        position = internal_position(self.position_var.get())

        try:
            start_age = float(str(self.start_age_var.get()).replace(",", "."))
        except Exception:
            start_age = 15.0

        # 2.54: Assistenten arbejder med en periode i år i stedet for særskilt
        # start- og slutalder. Startalderen kommer fra kontrolcenteret; standard-
        # perioden er 10 år.
        period_years = 10.0
        end_age = start_age + period_years
        ebr_enabled, ebr_ratio, ebr_knee = self._get_experience_bonus_settings()

        player_link = self._current_player_link_reference() if hasattr(self, "player_link_var") else ""
        player_name = getattr(self, "_player_link_display_value", "") or ""
        if player_name in {"Ugyldigt link", "Avatar ikke hentet", "Avatar ikke vist", "Ingen avatar"}:
            player_name = ""

        return {
            "position": position,
            "start_age": start_age,
            "end_age": end_age,
            "period_years": period_years,
            "start_stats": self._get_start_stats(),
            "ebr_enabled": ebr_enabled,
            "ebr_ratio": ebr_ratio,
            "ebr_knee": ebr_knee,
            "weights": dict(POSITION_WEIGHTS[position]),
            "change_every_days": 2,
            "allow_intensity_boost": False,
            "intensity_boost_uses": 1,
            "intensity_boost_period_days": 7,
            "intensity_boost_every_days": 7,
            "group_high_weighted_stats": True,
            "penalty_tolerance": "Høj",
            "roll_enabled": False,
            "roll_block_days": 7,
            "training_match_prelude_enabled": False,
            "training_match_until_age": min(17.0, end_age),
            "search_method": "Beam Search",
            "search_time": "5 min",
            "xp_variance": False,
            "xp": self.xp_var.get(),
            "xp_reference_intensity": internal_intensity(self.xp_reference_intensity_var.get()),
            "player_link": player_link,
            "player_name": player_name,
            "_player_import_status": player_name,
            "search_results_pool": [],
            "top_results": [],
            "_assistant_workspace_locked": False,
            "_assistant_reopen_step": 0,
            "_search_has_run": False,
        }

    def _assistant_has_search_state(self):
        data = getattr(self, "assistant_data", {}) or {}
        return bool(
            data.get("_search_has_run")
            or data.get("search_results_pool")
            or data.get("top_results")
        )

    def _assistant_has_persistent_workspace(self):
        data = getattr(self, "assistant_data", {}) or {}
        return bool(data.get("_assistant_workspace_locked") or self._assistant_has_search_state())

    def _assistant_clear_search_state(self):
        data = getattr(self, "assistant_data", {}) or {}
        data["search_results_pool"] = []
        data["top_results"] = []
        data.pop("continue_from_signature", None)
        data["_search_has_run"] = False
        # Side 1 og 2 bevares; kun selve søgningen ryddes.
        data["_assistant_workspace_locked"] = True
        data["_assistant_reopen_step"] = min(getattr(self, "assistant_step", 1), 1)
        self.assistant_data = data
        if hasattr(self, "assistant_top_tree"):
            self._assistant_fill_top_results([])
        if hasattr(self, "assistant_search_status_var"):
            self.assistant_search_status_var.set(self._tr("Søgningen er ryddet. Justér side 1/2 og start en ny søgning."))

    def _assistant_clear_search_from_button(self):
        """Ryd Assistentens søgeresultater fra side 3 uden at forlade siden."""
        try:
            self._assistant_stop_simulator_search()
        except Exception:
            pass
        self._assistant_clear_search_state()
        self.assistant_step = 2
        if not hasattr(self, "assistant_data") or self.assistant_data is None:
            self.assistant_data = {}
        self.assistant_data["_assistant_reopen_step"] = 2
        self.assistant_data["_assistant_workspace_locked"] = True
        try:
            self._render_assistant_step()
        except Exception:
            pass

    def _assistant_confirm_clear_search_before_back(self):
        if getattr(self, "assistant_step", 0) != 2 or not self._assistant_has_search_state():
            return True
        return messagebox.askyesno(
            self._tr("Ryd Assistent-søgning?"),
            self._tr("Hvis du går tilbage fra side 3, ryddes den aktuelle søgning og søgeresultaterne.\n\nVil du fortsætte?"),
            parent=self.assistant_window if self.assistant_window is not None else self,
        )

    def _open_assistant(self):
        if self.assistant_window is not None and self.assistant_window.winfo_exists():
            self.assistant_window.lift()
            self.assistant_window.focus_force()
            return

        if self._assistant_has_persistent_workspace():
            self.assistant_step = max(0, min(2, int(self.assistant_data.get("_assistant_reopen_step", 2))))
        else:
            self.assistant_data = self._assistant_snapshot_from_ui()
            self.assistant_step = 0

        win = tk.Toplevel(self)
        win.title(self._tr("Assistent"))
        win.geometry("900x680")
        win.minsize(820, 600)
        win.transient(self)

        self.assistant_window = win
        win.protocol("WM_DELETE_WINDOW", self._close_assistant)

        self.assistant_outer = ttk.Frame(win, padding=12)
        self.assistant_outer.pack(fill="both", expand=True)

        self.assistant_title_var = tk.StringVar()
        ttk.Label(self.assistant_outer, textvariable=self.assistant_title_var, font=("", 16, "bold")).pack(anchor="w", pady=(0, 8))

        self.assistant_body = ttk.Frame(self.assistant_outer)
        self.assistant_body.pack(fill="both", expand=True)

        nav = ttk.Frame(self.assistant_outer)
        nav.pack(fill="x", pady=(10, 0))

        self.assistant_back_button = ttk.Button(nav, text="Tilbage", command=self._assistant_back)
        self.assistant_back_button.pack(side="left")

        self.assistant_next_button = ttk.Button(nav, text="Næste", command=self._assistant_next)
        self.assistant_next_button.pack(side="right")

        self.assistant_nav_save_button = ttk.Button(nav, text="Gem søgning", command=self._assistant_save_search)
        self.assistant_nav_load_button = ttk.Button(nav, text="Indlæs", command=self._assistant_load_search)

        self.assistant_close_button = ttk.Button(nav, text="Luk", command=self._close_assistant)
        self.assistant_close_button.pack(side="right", padx=(0, 8))

        self._render_assistant_step()

    def _close_assistant(self):
        try:
            if self.assistant_window is not None and self.assistant_window.winfo_exists():
                self._assistant_commit_current_step()
        except Exception:
            # Lukning må ikke blokeres af en valideringsfejl; nuværende data bevares bedst muligt.
            pass

        stop_event = getattr(self, "assistant_sim_stop_event", None)
        if stop_event is not None:
            stop_event.set()

        if self._assistant_has_persistent_workspace():
            self.assistant_data["_assistant_reopen_step"] = max(0, min(2, getattr(self, "assistant_step", 2)))

        if self.assistant_window is not None and self.assistant_window.winfo_exists():
            self.assistant_window.destroy()
        self.assistant_window = None

    def _assistant_clear_body(self):
        for child in self.assistant_body.winfo_children():
            child.destroy()

    def _assistant_back(self):
        try:
            self._assistant_commit_current_step()
            if self.assistant_step > 0:
                if self.assistant_step == 2 and self._assistant_has_search_state():
                    if not self._assistant_confirm_clear_search_before_back():
                        return
                    self._assistant_stop_simulator_search()
                    self._assistant_clear_search_state()
                self.assistant_step -= 1
                self.assistant_data["_assistant_reopen_step"] = self.assistant_step
                self._render_assistant_step()
        except Exception as e:
            self._assistant_handle_error(e)

    def _assistant_next(self):
        try:
            self._assistant_commit_current_step()
            if self.assistant_step < 2:
                self.assistant_step += 1
                if self.assistant_step == 2:
                    self.assistant_data["_assistant_workspace_locked"] = True
                    self.assistant_data["_assistant_reopen_step"] = 2
                else:
                    self.assistant_data["_assistant_reopen_step"] = self.assistant_step
                self._render_assistant_step()
            else:
                # Side 3 har sin egen Start/Stop-knap i Søgestrategi-sektionen.
                self._assistant_commit_search_step()
        except Exception as e:
            self._assistant_handle_error(e)

    def _assistant_handle_error(self, error):
        try:
            log_path = Path(__file__).resolve().parents[1] / "vman_error_log.txt"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n--- Assistent navigation error ---\n")
                f.write(traceback.format_exc())
                f.write("\n")
        except Exception:
            pass

        messagebox.showerror(
            self._tr("Assistent-fejl"),
            f"{self._tr('Assistenten kunne ikke gå videre.')}\n\n{error}",
            parent=self.assistant_window if self.assistant_window is not None else self,
        )

    def _render_assistant_step(self):
        self._assistant_clear_body()
        if self.assistant_step == 0:
            self.assistant_title_var.set("Assistent · 1/3 · Scenario")
            self._render_assistant_scenario()
        elif self.assistant_step == 1:
            self.assistant_title_var.set("Assistent · 2/3 · Mål og begrænsninger")
            self._render_assistant_weights()
        else:
            self.assistant_title_var.set("Assistent · 3/3 · Søgestrategi")
            self._render_assistant_search()

        try:
            if self.assistant_step <= 0:
                self.assistant_back_button.pack_forget()
            else:
                try:
                    self.assistant_back_button.pack_info()
                except Exception:
                    self.assistant_back_button.pack(side="left")
                self.assistant_back_button.config(state="normal")
        except Exception:
            pass
        self._assistant_update_nav_buttons()
        self._translate_widget_tree(self.assistant_window)
        self._apply_language_to_tree_headings()
        try:
            if hasattr(self, "assistant_title_var"):
                self.assistant_title_var.set(self._tr(self.assistant_title_var.get()))
        except Exception:
            pass

    def _assistant_update_nav_buttons(self):
        for button in (
            getattr(self, "assistant_next_button", None),
            getattr(self, "assistant_nav_save_button", None),
            getattr(self, "assistant_nav_load_button", None),
            getattr(self, "assistant_close_button", None),
        ):
            if button is not None:
                try:
                    button.pack_forget()
                except Exception:
                    pass

        if self.assistant_step == 2:
            # På side 3 er start/stop flyttet op i Søgestrategi-sektionen.
            # Bundens højre hjørne bruges til at gemme/indlæse søgningen.
            self.assistant_nav_save_button.pack(side="right")
            self.assistant_nav_load_button.pack(side="right", padx=(0, 6))
            self.assistant_close_button.pack(side="right", padx=(0, 8))
        else:
            self.assistant_next_button.config(text=self._tr("Næste"), state="normal")
            self.assistant_next_button.pack(side="right")
            self.assistant_close_button.pack(side="right", padx=(0, 8))


    def _assistant_commit_current_step(self):
        if not hasattr(self, "assistant_data"):
            return

        step = getattr(self, "assistant_step", 0)

        if step == 0:
            self._assistant_commit_scenario_step()
        elif step == 1:
            self._assistant_commit_constraints_step()
        elif step == 2:
            self._assistant_commit_search_step()

    def _assistant_commit_scenario_step(self):
        if hasattr(self, "assistant_position_var"):
            self.assistant_data["position"] = internal_position(self.assistant_position_var.get())

        if hasattr(self, "assistant_start_age_year_var") and hasattr(self, "assistant_start_age_day_var"):
            self.assistant_data["start_age"] = self._age_from_year_day(
                self.assistant_start_age_year_var,
                self.assistant_start_age_day_var,
                fallback=self.assistant_data.get("start_age", 15.0),
            )
            if hasattr(self, "assistant_start_age_var"):
                self.assistant_start_age_var.set(f"{self.assistant_data['start_age']:.10f}")
        elif hasattr(self, "assistant_start_age_var"):
            try:
                self.assistant_data["start_age"] = float(str(self.assistant_start_age_var.get()).replace(",", "."))
            except Exception:
                pass

        if hasattr(self, "assistant_xp_var"):
            raw = str(self.assistant_xp_var.get()).strip()
            digits = "".join(ch for ch in raw if ch.isdigit())[:3]
            self.assistant_data["xp"] = digits or "0"
            try:
                self.assistant_xp_var.set(self.assistant_data["xp"])
            except Exception:
                pass

        if hasattr(self, "assistant_xp_reference_intensity_var"):
            value = internal_intensity(self.assistant_xp_reference_intensity_var.get())
            if value not in INTENSITY_OPTIONS:
                value = "Hård"
            self.assistant_data["xp_reference_intensity"] = value

        if hasattr(self, "assistant_period_years_var"):
            try:
                period_years = float(str(self.assistant_period_years_var.get()).replace(",", "."))
            except Exception:
                period_years = float(self.assistant_data.get("period_years", 10.0))
            period_years = max(1.0, min(25.0, period_years))
            self.assistant_data["period_years"] = period_years
            try:
                if abs(period_years - round(period_years)) < 1e-9:
                    self.assistant_period_years_var.set(str(int(round(period_years))))
                else:
                    self.assistant_period_years_var.set(f"{period_years:.1f}".replace(".", ","))
            except Exception:
                pass

        self.assistant_data["end_age"] = float(self.assistant_data.get("start_age", 15.0)) + float(self.assistant_data.get("period_years", 10.0))

        # Spillerinfo bevares fra kontrolcenteret, men Assistenten kan ikke hente link selv.
        if hasattr(self, "assistant_player_name_var"):
            self.assistant_data["player_name"] = str(self.assistant_player_name_var.get()).strip()
            self.assistant_data["_player_import_status"] = self.assistant_data["player_name"]
        self.assistant_data["player_link"] = self._current_player_link_reference() if hasattr(self, "player_link_var") else self.assistant_data.get("player_link", "")

        if hasattr(self, "assistant_stat_vars"):
            stats = {}
            for stat, var in self.assistant_stat_vars.items():
                try:
                    stats[stat] = max(0, min(100, int(round(float(str(var.get()).replace(",", "."))))))
                except Exception:
                    stats[stat] = 0.0
            self.assistant_data["start_stats"] = stats

        if hasattr(self, "assistant_ebr_enabled_var"):
            self.assistant_data["ebr_enabled"] = bool(self.assistant_ebr_enabled_var.get())

        if hasattr(self, "assistant_ebr_ratio_var"):
            try:
                ratio = float(str(self.assistant_ebr_ratio_var.get()).replace(",", "."))
            except Exception:
                ratio = self.assistant_data.get("ebr_ratio", 1.10)
            self.assistant_data["ebr_ratio"] = max(1.01, min(EBR_RATIO_MAX, ratio))

        if hasattr(self, "assistant_ebr_knee_var"):
            knee = self.assistant_ebr_knee_var.get()
            if knee not in {"early", "linear", "late"}:
                knee = "linear"
            self.assistant_data["ebr_knee"] = knee

        position = internal_position(self.assistant_data.get("position", self.position_var.get()))
        current_weights = self.assistant_data.get("weights", {})
        expected_stats = set(POSITION_STATS[position])
        if set(current_weights.keys()) != expected_stats:
            self.assistant_data["weights"] = dict(POSITION_WEIGHTS[position])

    def _assistant_commit_constraints_step(self):
        if hasattr(self, "assistant_ebr_enabled_var"):
            self.assistant_data["ebr_enabled"] = bool(self.assistant_ebr_enabled_var.get())

        if hasattr(self, "assistant_ebr_ratio_var"):
            try:
                ratio = float(str(self.assistant_ebr_ratio_var.get()).replace(",", "."))
            except Exception:
                ratio = self.assistant_data.get("ebr_ratio", 1.10)
            self.assistant_data["ebr_ratio"] = max(1.01, min(EBR_RATIO_MAX, ratio))

        if hasattr(self, "assistant_ebr_knee_var"):
            knee = self.assistant_ebr_knee_var.get()
            if knee not in {"early", "linear", "late"}:
                knee = "linear"
            self.assistant_data["ebr_knee"] = knee

        if hasattr(self, "assistant_weight_vars"):
            weights = {}
            for stat, var in self.assistant_weight_vars.items():
                try:
                    weights[stat] = max(0.0, float(str(var.get()).replace(",", ".")))
                except Exception:
                    weights[stat] = 0.0

            total = sum(weights.values())
            if total > 0:
                weights = {stat: value * 100.0 / total for stat, value in weights.items()}

            self.assistant_data["weights"] = weights

        if hasattr(self, "assistant_change_every_days_var"):
            try:
                self.assistant_data["change_every_days"] = max(
                    1,
                    int(float(str(self.assistant_change_every_days_var.get()).replace(",", "."))),
                )
            except Exception:
                pass

        if hasattr(self, "assistant_allow_intensity_var"):
            self.assistant_data["allow_intensity_boost"] = bool(self.assistant_allow_intensity_var.get())

        if hasattr(self, "assistant_intensity_boost_uses_var") or hasattr(self, "assistant_intensity_boost_period_var"):
            self._assistant_validate_intensity_fraction()

        if hasattr(self, "assistant_intensity_boost_period_var"):
            try:
                period_days = max(1, int(float(str(self.assistant_intensity_boost_period_var.get()).replace(",", "."))))
            except Exception:
                period_days = int(self.assistant_data.get("intensity_boost_period_days", self.assistant_data.get("intensity_boost_every_days", 7)))
            period_days = max(1, min(365, period_days))
            self.assistant_data["intensity_boost_period_days"] = period_days
            self.assistant_data["intensity_boost_every_days"] = period_days

        if hasattr(self, "assistant_intensity_boost_uses_var"):
            try:
                uses = max(1, int(float(str(self.assistant_intensity_boost_uses_var.get()).replace(",", "."))))
            except Exception:
                uses = int(self.assistant_data.get("intensity_boost_uses", 1))
            period_days = int(self.assistant_data.get("intensity_boost_period_days", self.assistant_data.get("intensity_boost_every_days", 7)))
            self.assistant_data["intensity_boost_uses"] = max(1, min(period_days, uses))

        if hasattr(self, "assistant_group_high_weighted_var"):
            self.assistant_data["group_high_weighted_stats"] = bool(self.assistant_group_high_weighted_var.get())
        elif "group_high_weighted_stats" not in self.assistant_data:
            self.assistant_data["group_high_weighted_stats"] = True

        if hasattr(self, "assistant_penalty_tolerance_var"):
            value = internal_penalty_tolerance(self.assistant_penalty_tolerance_var.get())
            if value not in {"Ingen", "Lav", "Mellem", "Høj"}:
                value = "Høj"
            self.assistant_data["penalty_tolerance"] = value
        elif "penalty_tolerance" not in self.assistant_data:
            self.assistant_data["penalty_tolerance"] = "Høj"

        if hasattr(self, "assistant_roll_enabled_var"):
            self.assistant_data["roll_enabled"] = bool(self.assistant_roll_enabled_var.get())
        if hasattr(self, "assistant_roll_block_days_var"):
            try:
                self.assistant_data["roll_block_days"] = max(1, min(365, int(float(str(self.assistant_roll_block_days_var.get()).replace(",", ".")))))
            except Exception:
                self.assistant_data["roll_block_days"] = int(self.assistant_data.get("roll_block_days", 7) or 21)

        if hasattr(self, "assistant_tm_prelude_enabled_var"):
            self.assistant_data["training_match_prelude_enabled"] = bool(self.assistant_tm_prelude_enabled_var.get())
        if hasattr(self, "assistant_tm_until_year_var") and hasattr(self, "assistant_tm_until_day_var"):
            fallback = float(self.assistant_data.get("training_match_until_age", self.assistant_data.get("start_age", 15.0)))
            value = self._assistant_age_value_from_vars(self.assistant_tm_until_year_var, self.assistant_tm_until_day_var, fallback=fallback)
            start_age = float(self.assistant_data.get("start_age", 15.0))
            end_age = float(self.assistant_data.get("end_age", max(start_age, value)))
            value = max(start_age, min(end_age, value))
            self.assistant_data["training_match_until_age"] = value
            self._set_age_part_vars(self.assistant_tm_until_year_var, self.assistant_tm_until_day_var, value)

    def _assistant_on_search_method_change(self, event=None):
        """Brute Force er manuel og skal stoppes med Stop simulator."""
        try:
            method = self.assistant_search_method_var.get()
            if method == "Brute Force":
                self.assistant_search_time_var.set(display_search_time("Manuel", self._language_code()))
                if hasattr(self, "assistant_search_time_box"):
                    self.assistant_search_time_box.configure(state="disabled")
            else:
                if hasattr(self, "assistant_search_time_box"):
                    self.assistant_search_time_box.configure(state="readonly")
                if internal_search_time(self.assistant_search_time_var.get()) == "Manuel":
                    self.assistant_search_time_var.set(display_search_time("5 min", self._language_code()))
        except Exception:
            pass

    def _assistant_commit_search_step(self):
        self.assistant_data["_assistant_workspace_locked"] = True
        self.assistant_data["_assistant_reopen_step"] = 2

        if hasattr(self, "assistant_search_method_var"):
            self.assistant_data["search_method"] = self.assistant_search_method_var.get()

        if self.assistant_data.get("search_method") == "Brute Force":
            self.assistant_data["search_time"] = "Manuel"
            if hasattr(self, "assistant_search_time_var"):
                self.assistant_search_time_var.set(display_search_time("Manuel", self._language_code()))
        elif hasattr(self, "assistant_search_time_var"):
            self.assistant_data["search_time"] = internal_search_time(self.assistant_search_time_var.get())

        if hasattr(self, "assistant_xp_variance_var"):
            self.assistant_data["xp_variance"] = bool(self.assistant_xp_variance_var.get())


    def _assistant_selected_position_stats(self):
        position = internal_position(self.assistant_data.get("position", self.position_var.get()))
        return POSITION_STATS[position]

    def _make_age_spin_pair(self, parent, age_value, command=None):
        year, day = self._split_age(age_value)
        frame = ttk.Frame(parent)
        year_var = tk.StringVar(value=str(year))
        day_var = tk.StringVar(value=str(day))
        year_spin = tk.Spinbox(frame, from_=15, to=40, textvariable=year_var, width=4, command=command)
        year_spin.pack(side="left")
        self._configure_numeric_widget(year_spin, integer=True, max_value=40)
        ttk.Label(frame, text="år").pack(side="left", padx=(4, 6))
        day_spin = tk.Spinbox(frame, from_=0, to=29, textvariable=day_var, width=3, command=command)
        day_spin.pack(side="left")
        self._configure_numeric_widget(day_spin, integer=True, max_value=29)
        ttk.Label(frame, text="dage").pack(side="left", padx=(4, 0))
        return frame, year_var, day_var, year_spin, day_spin

    def _assistant_age_value_from_vars(self, year_var, day_var, fallback=17.0):
        return self._age_from_year_day(year_var, day_var, fallback=fallback)

    def _assistant_player_name_from_main(self):
        name = str(getattr(self, "_player_link_display_value", "") or "").strip()
        if name in {"Ugyldigt link", "Avatar ikke hentet", "Avatar ikke vist", "Ingen avatar", "Ingen spiller hentet"}:
            return ""
        raw_link = str(getattr(self, "_player_link_raw_value", "") or "").strip()
        # Der skal kun stå navn, hvis der faktisk er hentet/importeret en spiller.
        if not raw_link and getattr(self, "_player_link_display_mode", "raw") != "display":
            return ""
        return name

    def _assistant_reset_scenario_page(self):
        """Ryd kun Assistentens side 1, uden at rydde kontrolcenteret."""
        self.assistant_data["position"] = "Keepere"
        self.assistant_data["start_age"] = 15.0
        self.assistant_data["end_age"] = 25.0
        self.assistant_data["period_years"] = 10.0
        self.assistant_data["xp"] = "180"
        self.assistant_data["xp_reference_intensity"] = "Hård"
        self.assistant_data["start_stats"] = {stat: 2 for stat in POSITION_STATS["Keepere"]}
        self.assistant_data["weights"] = dict(POSITION_WEIGHTS["Keepere"])
        self.assistant_data["player_name"] = ""
        self.assistant_data["_player_import_status"] = ""
        self.assistant_data["player_link"] = ""
        self._render_assistant_step()

    def _assistant_update_start_summary_label(self, *_):
        if not hasattr(self, "assistant_start_rating_var"):
            return

        try:
            position = internal_position(
                self.assistant_position_var.get()
                if hasattr(self, "assistant_position_var")
                else self.assistant_data.get("position", self.position_var.get())
            )
            stats = POSITION_STATS[position]
            values = {}
            for stat in stats:
                var = getattr(self, "assistant_stat_vars", {}).get(stat)
                if var is None:
                    values[stat] = max(0, min(100, int(round(float(self.assistant_data.get("start_stats", {}).get(stat, 2.0))))))
                else:
                    try:
                        values[stat] = max(0, min(100, int(round(float(str(var.get()).replace(",", "."))))))
                    except Exception:
                        values[stat] = 0.0

            state = PlayerState(stats=values, stat_names=stats)
            summary = summarize_state(state, position=position)
            rating = float(summary.get(f"Vurderingstal_{position}", 0.0))
            total = sum(float(summary.get(stat, 0.0)) for stat in stats)

            self.assistant_start_rating_var.set(f"{rating:.2f}")
            if abs(total - round(total)) < 0.005:
                self.assistant_start_stat_sum_var.set(str(int(round(total))))
            else:
                self.assistant_start_stat_sum_var.set(f"{total:.2f}")
        except Exception:
            self.assistant_start_rating_var.set("-")
            if hasattr(self, "assistant_start_stat_sum_var"):
                self.assistant_start_stat_sum_var.set("-")

    def _assistant_age_short_text(self, age):
        """Formatér alder som år.dage, fx 15.3."""
        years, days = self._split_age(age)
        return f"{years}.{days}"

    def _assistant_update_training_period_label(self, *_):
        if not hasattr(self, "assistant_training_period_range_var"):
            return

        try:
            start_age = self._age_from_year_day(
                self.assistant_start_age_year_var,
                self.assistant_start_age_day_var,
                fallback=self.assistant_data.get("start_age", 15.0),
            )
        except Exception:
            start_age = float(self.assistant_data.get("start_age", 15.0))

        try:
            period_years = float(str(self.assistant_period_years_var.get()).replace(",", "."))
        except Exception:
            period_years = float(self.assistant_data.get("period_years", 10.0))

        end_age = start_age + period_years
        prefix = self._tr("frem til")
        self.assistant_training_period_range_var.set(
            f"{prefix} {self._format_age_parts(end_age)}"
        )

    def _render_assistant_scenario(self):
        frame = ttk.Frame(self.assistant_body)
        frame.pack(fill="both", expand=True)

        top = ttk.LabelFrame(frame, text="Spiller", padding=10)
        top.pack(fill="x", pady=(0, 10))
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=0)

        # 3.60: Genkald seneste søgning er fjernet. Hvis Kontrolcenter ændres
        # efter en Assistent-søgning, advares brugeren i stedet, og søgningen
        # nulstilles bevidst.
        start_grid_row = 0

        row_gap = (2, 5)

        player_fields_frame = ttk.Frame(top)
        player_fields_frame.grid(row=start_grid_row, column=0, sticky="new", padx=(0, 10))
        player_fields_frame.columnconfigure(0, weight=0)
        player_fields_frame.columnconfigure(1, weight=1)

        # Spillerens navn og billede kommer fra kontrolcenteret. Assistenten har ikke
        # længere eget spillerlinkfelt eller "Hent spiller"-knap.
        player_name = str(self.assistant_data.get("player_name", "") or "").strip()
        if not player_name:
            player_name = self._assistant_player_name_from_main()
        if player_name in {"Ugyldigt link", "Ingen spiller hentet"}:
            player_name = ""

        self.assistant_player_name_var = tk.StringVar(value=player_name)
        ttk.Label(player_fields_frame, text="Spiller").grid(row=0, column=0, sticky="w", pady=row_gap)
        ttk.Label(
            player_fields_frame,
            textvariable=self.assistant_player_name_var,
            font=("", 11, "bold"),
        ).grid(row=0, column=1, sticky="w", padx=(4, 0), pady=row_gap)

        ttk.Label(player_fields_frame, text="Position").grid(row=1, column=0, sticky="w", pady=row_gap)
        self.assistant_position_var = tk.StringVar(value=self._display_position(self.assistant_data["position"]))
        pos_box = self._create_fixed_dropdown(
            player_fields_frame,
            self.assistant_position_var,
            values=self._position_options(),
            width_px=98,
        )
        self.assistant_position_box = pos_box
        pos_box.grid(row=1, column=1, sticky="w", padx=(4, 0), pady=row_gap)
        pos_box.bind("<<ComboboxSelected>>", lambda event: self._assistant_change_position())

        ttk.Label(player_fields_frame, text="Alder").grid(row=2, column=0, sticky="w", pady=row_gap)
        start_age = float(self.assistant_data.get("start_age", self.start_age_var.get() or 15.0))
        self.assistant_start_age_var = tk.StringVar(value=f"{start_age:.10f}")
        self.assistant_start_age_year_var = tk.StringVar(value="15")
        self.assistant_start_age_day_var = tk.StringVar(value="0")
        self._set_assistant_age_value("start", start_age)
        age_frame = ttk.Frame(player_fields_frame)
        age_frame.grid(row=2, column=1, sticky="w", padx=(4, 0), pady=row_gap)
        self.assistant_start_age_year_spinbox = tk.Spinbox(
            age_frame,
            from_=15,
            to=40,
            textvariable=self.assistant_start_age_year_var,
            width=int(getattr(self, "_main_numeric_field_width", 4)),
            command=lambda: self._assistant_age_parts_changed("start"),
        )
        self.assistant_start_age_year_spinbox.pack(side="left")
        self._configure_numeric_widget(self.assistant_start_age_year_spinbox, integer=True, max_value=40)
        ttk.Label(age_frame, text="år").pack(side="left", padx=(4, 8))
        self.assistant_start_age_day_spinbox = tk.Spinbox(
            age_frame,
            from_=0,
            to=29,
            textvariable=self.assistant_start_age_day_var,
            width=int(getattr(self, "_main_numeric_field_width", 4)),
            command=lambda: self._assistant_age_parts_changed("start"),
        )
        self.assistant_start_age_day_spinbox.pack(side="left")
        self._configure_numeric_widget(self.assistant_start_age_day_spinbox, integer=True, max_value=29)
        ttk.Label(age_frame, text="dage").pack(side="left", padx=(4, 0))
        self.assistant_start_age_year_var.trace_add("write", lambda *_: self._assistant_age_parts_changed("start"))
        self.assistant_start_age_day_var.trace_add("write", lambda *_: self._assistant_age_parts_changed("start"))
        self.assistant_start_age_year_var.trace_add("write", self._assistant_update_training_period_label)
        self.assistant_start_age_day_var.trace_add("write", self._assistant_update_training_period_label)

        ttk.Label(player_fields_frame, text="XP").grid(row=3, column=0, sticky="w", pady=row_gap)
        self.assistant_xp_var = tk.StringVar(value=str(self.assistant_data.get("xp", self.xp_var.get())))
        xp_row_frame = ttk.Frame(player_fields_frame)
        xp_row_frame.grid(row=3, column=1, sticky="w", padx=(4, 0), pady=row_gap)
        self.assistant_xp_spinbox = tk.Spinbox(
            xp_row_frame,
            from_=0,
            to=999,
            textvariable=self.assistant_xp_var,
            width=int(getattr(self, "_main_numeric_field_width", 4)),
        )
        self.assistant_xp_spinbox.pack(side="left")
        self._configure_numeric_widget(self.assistant_xp_spinbox, integer=True, max_value=999)
        self.assistant_xp_at_label = ttk.Label(xp_row_frame, text="ved", width=3, anchor="center")
        self.assistant_xp_at_label.pack(side="left", padx=(3, 4))
        self.assistant_xp_reference_intensity_var = tk.StringVar(
            value=self._display_intensity(internal_intensity(self.assistant_data.get("xp_reference_intensity", self.xp_reference_intensity_var.get())))
        )
        self.assistant_xp_reference_intensity_box = self._create_fixed_intensity_dropdown(
            xp_row_frame,
            self.assistant_xp_reference_intensity_var,
            None,
        )
        self.assistant_xp_reference_intensity_box._vman_lowercase_display = True
        self.assistant_xp_reference_intensity_box._vman_lowercase_menu = True
        self.assistant_xp_reference_intensity_box.pack(side="left")
        self.assistant_xp_intensity_suffix_parent = xp_row_frame
        self.assistant_xp_intensity_suffix_label = ttk.Label(xp_row_frame, text=self._tr("intensitet"))
        self.assistant_xp_intensity_suffix_label.pack(side="left", padx=(3, 0))
        self.after_idle(self._sync_intensity_suffix_labels)

        ttk.Label(player_fields_frame, text="Træningsperiode").grid(row=4, column=0, sticky="w", pady=row_gap)
        period_value = self.assistant_data.get("period_years", 10)
        try:
            period_float = float(period_value)
        except Exception:
            period_float = 10.0
        if abs(period_float - round(period_float)) < 1e-9:
            period_text = str(int(round(period_float)))
        else:
            period_text = f"{period_float:.1f}".replace(".", ",")
        self.assistant_period_years_var = tk.StringVar(value=period_text)
        period_frame = ttk.Frame(player_fields_frame)
        period_frame.grid(row=4, column=1, sticky="w", padx=(4, 0), pady=row_gap)
        self.assistant_period_years_spinbox = tk.Spinbox(
            period_frame,
            from_=1,
            to=25,
            textvariable=self.assistant_period_years_var,
            width=int(getattr(self, "_main_numeric_field_width", 4)),
            command=self._assistant_update_training_period_label,
        )
        self.assistant_period_years_spinbox.pack(side="left")
        self._configure_numeric_widget(self.assistant_period_years_spinbox, decimals=2, max_value=25)
        ttk.Label(period_frame, text="år").pack(side="left", padx=(4, 8))
        self.assistant_training_period_range_var = tk.StringVar(value="")
        ttk.Label(period_frame, textvariable=self.assistant_training_period_range_var).pack(side="left")
        self.assistant_period_years_var.trace_add("write", self._assistant_update_training_period_label)
        self._assistant_update_training_period_label()

        avatar_box_width = int(getattr(self, "_player_avatar_box_width", 92))
        avatar_box_height = int(getattr(self, "_player_avatar_box_height", 112))
        self.assistant_avatar_canvas = tk.Canvas(
            top,
            width=avatar_box_width,
            height=avatar_box_height,
            background=(getattr(self, "_theme_panel_bg", "#2f2b2b")),
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#ffffff",
            highlightcolor="#ffffff",
        )
        self.assistant_avatar_canvas.grid(row=start_grid_row, column=1, sticky="ne", padx=(12, 0), pady=(0, 0))
        assistant_photo = getattr(self, "player_avatar_photo", None) or self._make_generic_avatar_photo()
        self._draw_avatar_photo_on_canvas(self.assistant_avatar_canvas, assistant_photo)

        # Bevar link/status internt fra kontrolcenteret, men vis ikke hentefelt i Assistent.
        self.assistant_data["player_link"] = self._current_player_link_reference() if hasattr(self, "player_link_var") else self.assistant_data.get("player_link", "")
        self.assistant_data["player_name"] = player_name
        self.assistant_data["_player_import_status"] = player_name

        stats_frame = ttk.LabelFrame(frame, text="Egenskaber", padding=10)
        stats_frame.pack(fill="both", expand=True)

        self.assistant_stat_vars = {}
        self._assistant_render_start_stats(stats_frame)

    def _assistant_render_ebr_controls(self, parent, row=0):
        ebr = ttk.Frame(parent)
        ebr.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        ebr.columnconfigure(7, weight=1)

        self.assistant_ebr_enabled_var = tk.BooleanVar(value=bool(self.assistant_data.get("ebr_enabled", False)))
        self.assistant_ebr_check = ttk.Checkbutton(
            ebr,
            text="Erfaringsbonus",
            variable=self.assistant_ebr_enabled_var,
            command=self._assistant_on_ebr_toggle,
        )
        self.assistant_ebr_check.grid(row=0, column=0, sticky="w")

        ttk.Label(ebr, text="Ratio").grid(row=0, column=1, sticky="e", padx=(18, 4))
        self.assistant_ebr_ratio_var = tk.StringVar(value=f"{min(EBR_RATIO_MAX, max(1.01, float(self.assistant_data.get('ebr_ratio', 1.10)))):.2f}")
        self.assistant_ebr_ratio_spinbox = tk.Spinbox(
            ebr,
            from_=1.01,
            to=EBR_RATIO_MAX,
            increment=0.01,
            format="%.2f",
            textvariable=self.assistant_ebr_ratio_var,
            width=6,
            state="normal" if self.assistant_ebr_enabled_var.get() else "disabled",
        )
        self.assistant_ebr_ratio_spinbox.grid(row=0, column=2, sticky="w")
        self._configure_numeric_widget(self.assistant_ebr_ratio_spinbox, decimals=2, max_value=EBR_RATIO_MAX)

        ttk.Label(ebr, text="Gradient").grid(row=0, column=3, sticky="w", padx=(18, 4))
        self.assistant_ebr_knee_var = tk.StringVar(value=self.assistant_data.get("ebr_knee", "linear"))
        self.assistant_ebr_knee_canvases = []

        self.assistant_ebr_knee_frame = ttk.Frame(ebr)
        self.assistant_ebr_knee_frame.grid(row=0, column=4, sticky="w")

        for value in ("early", "linear", "late"):
            canvas = tk.Canvas(
                self.assistant_ebr_knee_frame,
                width=29,
                height=19,
                highlightthickness=1,
                highlightbackground=(getattr(self, "_theme_panel_border", "#474242")),
                background=(getattr(self, "_theme_panel_bg", "#2f2b2b")),
                cursor="arrow",
            )
            canvas.pack(side="left", padx=(0, 10))
            canvas.bind("<Button-1>", lambda event, v=value: self._assistant_select_ebr_knee(v))
            self.assistant_ebr_knee_canvases.append((value, canvas))

        self._redraw_assistant_ebr_knee_icons()

    def _assistant_on_ebr_toggle(self):
        enabled = bool(self.assistant_ebr_enabled_var.get()) if hasattr(self, "assistant_ebr_enabled_var") else False

        if hasattr(self, "assistant_ebr_ratio_spinbox"):
            self.assistant_ebr_ratio_spinbox.config(state="normal" if enabled else "disabled")

        self._redraw_assistant_ebr_knee_icons()

    def _assistant_select_ebr_knee(self, value):
        if hasattr(self, "assistant_ebr_enabled_var") and not self.assistant_ebr_enabled_var.get():
            return

        if hasattr(self, "assistant_ebr_knee_var"):
            self.assistant_ebr_knee_var.set(value)

        self._redraw_assistant_ebr_knee_icons()


    def _make_ebr_curve_icon_photo(self, kind, enabled, width, height):
        """Lav et lille anti-aliased gradient-ikon uden ekstra afhængigheder."""
        width = max(24, int(width or 46))
        height = max(16, int(height or 26))
        bg = (255, 255, 255) if enabled else (238, 238, 238)
        line = (34, 34, 34) if enabled else (154, 154, 154)
        # Centre the curve a bit more evenly inside the small icon box.
        # Keep the visual size roughly the same, but balance the margins so
        # the symbol does not sit slightly too far up/left.
        left = 7.0
        right = width - 7.0
        top = 5.0
        bottom = height - 5.0

        def y_norm(t):
            if kind == "early":
                return 1.0 - (1.0 - t) ** 2
            if kind == "late":
                return t ** 2
            return t

        pts = []
        for step in range(65):
            t = step / 64
            pts.append((left + t * (right - left), bottom - y_norm(t) * (bottom - top)))

        def dist_to_segment(px, py, ax, ay, bx, by):
            abx = bx - ax
            aby = by - ay
            denom = abx * abx + aby * aby
            if denom <= 0:
                return math.hypot(px - ax, py - ay)
            u = max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / denom))
            cx = ax + u * abx
            cy = ay + u * aby
            return math.hypot(px - cx, py - cy)

        def min_distance(px, py):
            best = 999.0
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                d = dist_to_segment(px, py, ax, ay, bx, by)
                if d < best:
                    best = d
                    if best <= 0.25:
                        break
            return best

        def blend(fg, bg, alpha):
            alpha = max(0.0, min(1.0, float(alpha)))
            return tuple(int(round(bg[i] * (1.0 - alpha) + fg[i] * alpha)) for i in range(3))

        def hex_color(rgb):
            return "#%02x%02x%02x" % rgb

        samples = ((0.20, 0.20), (0.80, 0.20), (0.20, 0.80), (0.80, 0.80), (0.50, 0.50))
        half_width = 1.15
        soft_edge = 0.95
        rows = []
        for y in range(height):
            row = []
            for x in range(width):
                coverage = 0.0
                for sx, sy in samples:
                    d = min_distance(x + sx, y + sy)
                    if d <= half_width:
                        coverage += 1.0
                    elif d <= half_width + soft_edge:
                        coverage += 1.0 - ((d - half_width) / soft_edge)
                alpha = coverage / len(samples)
                row.append(hex_color(blend(line, bg, alpha)))
            rows.append("{" + " ".join(row) + "}")

        photo = tk.PhotoImage(width=width, height=height)
        photo.put(" ".join(rows), to=(0, 0))
        return photo

    def _draw_ebr_curve_icon(self, canvas, kind, enabled):
        try:
            width = int(float(canvas.cget("width")))
            height = int(float(canvas.cget("height")))
            photo = self._make_ebr_curve_icon_photo(kind, enabled, width, height)
            canvas._ebr_curve_photo = photo
            canvas.create_image(0, 0, anchor="nw", image=photo)
            return True
        except Exception:
            return False

    def _redraw_assistant_ebr_knee_icons(self):
        if not hasattr(self, "assistant_ebr_knee_canvases"):
            return

        enabled = self.assistant_ebr_enabled_var.get() if hasattr(self, "assistant_ebr_enabled_var") else False
        selected = self.assistant_ebr_knee_var.get() if hasattr(self, "assistant_ebr_knee_var") else "linear"

        def curve_points(kind):
            pts = []
            for step in range(17):
                t = step / 16
                if kind == "early":
                    y_norm = 1.0 - (1.0 - t) ** 2
                elif kind == "late":
                    y_norm = t ** 2
                else:
                    y_norm = t

                x = 7 + t * 15
                y = 14 - y_norm * 9
                pts.extend([x, y])
            return pts

        for value, canvas in self.assistant_ebr_knee_canvases:
            canvas.delete("all")
            bg = "white" if enabled else "#eeeeee"
            outline = "#6aa0ff" if enabled and value == selected else "#b8b8b8"
            line = "#222222" if enabled else "#9a9a9a"
            canvas.configure(background=bg, highlightbackground=outline)
            if not self._draw_ebr_curve_icon(canvas, value, enabled):
                canvas.create_line(*curve_points(value), width=3, fill=line, smooth=True, splinesteps=24, capstyle=tk.ROUND)

    def _assistant_change_position(self):
        old_position = internal_position(self.assistant_data.get("position", self.position_var.get()))
        new_position = internal_position(self.assistant_position_var.get())
        if old_position == new_position: return
        old_stats = dict(self.assistant_data.get("start_stats", {}))
        self.assistant_data["position"] = new_position
        self.assistant_data["start_stats"] = {stat: int(round(float(old_stats.get(stat, 2.0)))) for stat in POSITION_STATS[new_position]}
        self.assistant_data["weights"] = dict(POSITION_WEIGHTS[new_position])
        self._render_assistant_step()

    def _assistant_render_start_stats(self, parent):
        stats = self._vman_stat_display_order(self._assistant_selected_position_stats())
        current = self.assistant_data.get("start_stats", {})
        rows_per_column = (len(stats) + 1) // 2

        for col in range(5):
            parent.columnconfigure(col, weight=0)
        parent.columnconfigure(4, weight=1)

        for i, stat in enumerate(stats):
            row = i % rows_per_column
            col_group = i // rows_per_column
            label_col = col_group * 2
            input_col = label_col + 1

            ttk.Label(parent, text=self._display_stat(stat)).grid(
                row=row,
                column=label_col,
                sticky="w",
                padx=(0 if col_group == 0 else 24, 11),
                pady=2,
            )
            value = current.get(stat, 2.0)
            try:
                value_float = float(value)
                if abs(value_float - round(value_float)) < 0.005:
                    value_text = str(int(round(value_float)))
                else:
                    value_text = f"{value_float:.1f}"
            except Exception:
                value_text = str(value)

            var = tk.StringVar(value=value_text)
            self.assistant_stat_vars[stat] = var
            spin = tk.Spinbox(
                parent,
                from_=0,
                to=100,
                textvariable=var,
                width=int(getattr(self, "_main_numeric_field_width", 4)),
                command=self._assistant_update_start_summary_label,
            )
            self._configure_numeric_widget(spin, integer=True, max_value=100)
            spin.grid(row=row, column=input_col, sticky="w", pady=2)
            var.trace_add("write", self._assistant_update_start_summary_label)

        rating_row = rows_per_column + 1
        ttk.Separator(parent, orient="horizontal").grid(
            row=rating_row - 1,
            column=0,
            columnspan=5,
            sticky="ew",
            pady=(8, 6),
        )

        summary_frame = ttk.Frame(parent)
        summary_frame.grid(row=rating_row, column=0, columnspan=5, sticky="w", pady=(0, 1))

        ttk.Label(summary_frame, text="Vurderingstal").pack(side="left")
        self.assistant_start_rating_var = tk.StringVar(value="-")
        ttk.Label(
            summary_frame,
            textvariable=self.assistant_start_rating_var,
            font=("", 12, "bold"),
        ).pack(side="left", padx=(8, 24))

        ttk.Label(summary_frame, text="Egenskabssum").pack(side="left")
        self.assistant_start_stat_sum_var = tk.StringVar(value="-")
        ttk.Label(
            summary_frame,
            textvariable=self.assistant_start_stat_sum_var,
            font=("", 12, "bold"),
        ).pack(side="left", padx=(8, 0))

        ttk.Button(
            summary_frame,
            text="Ryd",
            width=6,
            command=self._assistant_reset_scenario_page,
        ).pack(side="left", padx=(18, 0))

        self._assistant_update_start_summary_label()

    def _render_assistant_weights(self):
        frame = ttk.Frame(self.assistant_body)
        frame.pack(fill="both", expand=True)

        constraints = ttk.LabelFrame(frame, text="Praktiske begrænsninger", padding=10)
        constraints.pack(fill="x", pady=(0, 10))

        ttk.Label(constraints, text="Længde på træningspas").grid(row=0, column=0, sticky="w")
        self.assistant_change_every_days_var = tk.StringVar(value=str(self.assistant_data.get("change_every_days", 2)))
        training_pass_length_frame = ttk.Frame(constraints)
        training_pass_length_frame.grid(row=0, column=1, sticky="w", padx=(8, 24))
        self.assistant_change_every_days_spinbox = tk.Spinbox(
            training_pass_length_frame,
            from_=1,
            to=365,
            textvariable=self.assistant_change_every_days_var,
            width=6,
        )
        self._configure_numeric_widget(self.assistant_change_every_days_spinbox, integer=True, max_value=365)
        self.assistant_change_every_days_spinbox.pack(side="left")
        ttk.Label(training_pass_length_frame, text="dage").pack(side="left", padx=(4, 0))

        self.assistant_allow_intensity_var = tk.BooleanVar(value=bool(self.assistant_data.get("allow_intensity_boost", False)))
        self.assistant_intensity_check = ttk.Checkbutton(
            constraints,
            text="Tillad intensitetsforøgelse",
            variable=self.assistant_allow_intensity_var,
            command=self._assistant_toggle_intensity_controls,
        )
        self.assistant_intensity_check.grid(row=1, column=0, sticky="w", pady=(10, 0))

        self.assistant_intensity_fraction_frame = ttk.Frame(constraints)
        self.assistant_intensity_fraction_frame.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(10, 0))

        self.assistant_intensity_fraction_lock = False
        self.assistant_intensity_boost_uses_var = tk.StringVar(value=str(self.assistant_data.get("intensity_boost_uses", 1)))
        self.assistant_intensity_boost_period_var = tk.StringVar(value=str(self.assistant_data.get("intensity_boost_period_days", self.assistant_data.get("intensity_boost_every_days", 7))))

        self.assistant_intensity_boost_uses_spinbox = tk.Spinbox(
            self.assistant_intensity_fraction_frame,
            from_=1,
            to=max(1, int(float(str(self.assistant_intensity_boost_period_var.get()).replace(",", ".")))),
            textvariable=self.assistant_intensity_boost_uses_var,
            width=4,
            command=self._assistant_validate_intensity_fraction,
        )
        self.assistant_intensity_boost_uses_spinbox.pack(side="left")
        self._configure_numeric_widget(self.assistant_intensity_boost_uses_spinbox, integer=True, max_value=365)

        self.assistant_intensity_slash_label = ttk.Label(self.assistant_intensity_fraction_frame, text="/")
        self.assistant_intensity_slash_label.pack(side="left", padx=3)

        self.assistant_intensity_boost_period_spinbox = tk.Spinbox(
            self.assistant_intensity_fraction_frame,
            from_=1,
            to=365,
            textvariable=self.assistant_intensity_boost_period_var,
            width=5,
            command=self._assistant_validate_intensity_fraction,
        )
        self.assistant_intensity_boost_period_spinbox.pack(side="left")
        self._configure_numeric_widget(self.assistant_intensity_boost_period_spinbox, integer=True, max_value=365)

        self.assistant_intensity_days_label = ttk.Label(self.assistant_intensity_fraction_frame, text="dage")
        self.assistant_intensity_days_label.pack(side="left", padx=(4, 0))

        self.assistant_intensity_boost_uses_var.trace_add("write", lambda *_: self._assistant_validate_intensity_fraction())
        self.assistant_intensity_boost_period_var.trace_add("write", lambda *_: self._assistant_validate_intensity_fraction())
        self._assistant_validate_intensity_fraction()
        self._assistant_toggle_intensity_controls()

        self.assistant_roll_enabled_var = tk.BooleanVar(value=bool(self.assistant_data.get("roll_enabled", False)))
        ttk.Checkbutton(
            constraints,
            text="Fast rul",
            variable=self.assistant_roll_enabled_var,
            command=self._assistant_toggle_roll_controls,
        ).grid(row=2, column=0, sticky="w", pady=(10, 0))

        self.assistant_roll_frame = ttk.Frame(constraints)
        self.assistant_roll_frame.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        ttk.Label(self.assistant_roll_frame, text="bloklængde").pack(side="left")
        self.assistant_roll_block_days_var = tk.StringVar(value=str(self.assistant_data.get("roll_block_days", 7)))
        self.assistant_roll_block_days_spinbox = tk.Spinbox(
            self.assistant_roll_frame,
            from_=1,
            to=365,
            textvariable=self.assistant_roll_block_days_var,
            width=5,
        )
        self.assistant_roll_block_days_spinbox.pack(side="left", padx=(6, 4))
        self._configure_numeric_widget(self.assistant_roll_block_days_spinbox, integer=True, max_value=365)
        ttk.Label(self.assistant_roll_frame, text="dage").pack(side="left")

        self.assistant_tm_prelude_enabled_var = tk.BooleanVar(value=bool(self.assistant_data.get("training_match_prelude_enabled", False)))
        ttk.Checkbutton(
            constraints,
            text="Træningskamp",
            variable=self.assistant_tm_prelude_enabled_var,
            command=self._assistant_toggle_tm_prelude_controls,
        ).grid(row=3, column=0, sticky="w", pady=(10, 0))

        self.assistant_tm_until_frame = ttk.Frame(constraints)
        self.assistant_tm_until_frame.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        ttk.Label(self.assistant_tm_until_frame, text="frem til").pack(side="left", padx=(0, 6))
        tm_until = self.assistant_data.get("training_match_until_age", min(17.0, float(self.assistant_data.get("end_age", 26.0))))
        tm_age_frame, self.assistant_tm_until_year_var, self.assistant_tm_until_day_var, self.assistant_tm_until_year_spinbox, self.assistant_tm_until_day_spinbox = self._make_age_spin_pair(
            self.assistant_tm_until_frame,
            tm_until,
            command=self._assistant_normalize_tm_until_age,
        )
        tm_age_frame.pack(side="left")
        self.assistant_tm_until_year_var.trace_add("write", lambda *_: self._assistant_normalize_tm_until_age())
        self.assistant_tm_until_day_var.trace_add("write", lambda *_: self._assistant_normalize_tm_until_age())
        self._assistant_normalize_tm_until_age()

        self.assistant_group_high_weighted_var = tk.BooleanVar(
            value=bool(self.assistant_data.get("group_high_weighted_stats", True))
        )
        ttk.Checkbutton(
            constraints,
            text="Gruppér højtvægtede stats",
            variable=self.assistant_group_high_weighted_var,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        penalty_frame = ttk.Frame(constraints)
        penalty_frame.grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Label(penalty_frame, text="Straftolerance").pack(side="left")
        self.assistant_penalty_tolerance_var = tk.StringVar(value=display_penalty_tolerance(self.assistant_data.get("penalty_tolerance", "Høj"), self._language_code()))
        self.assistant_penalty_tolerance_box = self._create_fixed_dropdown(
            penalty_frame,
            self.assistant_penalty_tolerance_var,
            values=self._penalty_tolerance_options(),
            width_px=120,
        )
        self.assistant_penalty_tolerance_box.pack(side="left", padx=(6, 0))

        self._assistant_render_ebr_controls(constraints, row=6)

        self._assistant_toggle_roll_controls()
        self._assistant_toggle_tm_prelude_controls()

        weights_frame = ttk.LabelFrame(frame, text="Vægtninger", padding=10)
        weights_frame.pack(fill="both", expand=True)

        self.assistant_weight_vars = {}
        self.assistant_weight_last_values = {}
        self.assistant_weight_lock = False

        stats = self._assistant_selected_position_stats()
        weights = dict(self.assistant_data.get("weights", {}))
        total = sum(float(weights.get(stat, 0.0)) for stat in stats)

        if total <= 0:
            weights = {stat: 100.0 / len(stats) for stat in stats}
        else:
            weights = {stat: float(weights.get(stat, 0.0)) * 100.0 / total for stat in stats}

        columns = max(1, (len(stats) + 1) // 2)
        weights_topbar = ttk.Frame(weights_frame)
        weights_topbar.grid(row=0, column=0, columnspan=columns, sticky="ew", pady=(0, 8))
        weights_topbar.columnconfigure(0, weight=1)

        ttk.Label(weights_topbar, text="").grid(row=0, column=0, sticky="w")
        self.assistant_reset_weights_button = ttk.Button(
            weights_topbar,
            text="Nulstil",
            command=self._assistant_reset_weights,
        )
        self.assistant_reset_weights_button.grid(row=0, column=1, sticky="e")

        for col in range(columns):
            weights_frame.columnconfigure(col, weight=1)

        for i, stat in enumerate(stats):
            row = 1 + i // columns
            col = i % columns

            cell = ttk.Frame(weights_frame)
            cell.grid(row=row, column=col, sticky="w", padx=(0 if col == 0 else 12, 12), pady=(2, 8))

            ttk.Label(cell, text=self._display_stat(stat)).pack(anchor="w")

            initial_value = round(float(weights[stat]), 2)
            var = tk.StringVar(value=f"{initial_value:.2f}")
            self.assistant_weight_vars[stat] = var
            self.assistant_weight_last_values[stat] = initial_value

            spin_line = ttk.Frame(cell)
            spin_line.pack(anchor="w", pady=(2, 0))
            spinbox = tk.Spinbox(
                spin_line,
                from_=0.0,
                to=100.0,
                increment=0.1,
                format="%.2f",
                textvariable=var,
                width=7,
                command=lambda stat=stat: self._assistant_weight_changed(stat),
            )
            spinbox.pack(side="left")
            self._configure_numeric_widget(spinbox, decimals=2, max_value=100)
            ttk.Label(spin_line, text="%").pack(side="left", padx=(3, 0))

            spinbox.bind("<FocusOut>", lambda _event, stat=stat: self._assistant_weight_changed(stat))
            spinbox.bind("<Return>", lambda _event, stat=stat: self._assistant_weight_changed(stat))

    def _assistant_reset_weights(self):
        position = internal_position(self.assistant_data.get("position", self.position_var.get()))
        default_weights = dict(POSITION_WEIGHTS[position])

        self.assistant_data["weights"] = dict(default_weights)

        if hasattr(self, "assistant_weight_vars"):
            self.assistant_weight_lock = True
            try:
                for stat, value in default_weights.items():
                    if stat in self.assistant_weight_vars:
                        value = round(float(value), 2)
                        self.assistant_weight_last_values[stat] = value
                        self.assistant_weight_vars[stat].set(f"{value:.2f}")
            finally:
                self.assistant_weight_lock = False

    def _assistant_reset_page2(self):
        # Bagudkompatibel alias. Side 2 har nu kun vægt-nulstilling.
        self._assistant_reset_weights()


    def _assistant_toggle_roll_controls(self):
        enabled = bool(self.assistant_roll_enabled_var.get()) if hasattr(self, "assistant_roll_enabled_var") else False
        state = "normal" if enabled else "disabled"
        if hasattr(self, "assistant_roll_block_days_spinbox"):
            self.assistant_roll_block_days_spinbox.config(state=state)

    def _assistant_toggle_tm_prelude_controls(self):
        enabled = bool(self.assistant_tm_prelude_enabled_var.get()) if hasattr(self, "assistant_tm_prelude_enabled_var") else False
        state = "normal" if enabled else "disabled"
        for name in ("assistant_tm_until_year_spinbox", "assistant_tm_until_day_spinbox"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.config(state=state)

    def _assistant_normalize_tm_until_age(self):
        if getattr(self, "_assistant_tm_until_sync_lock", False):
            return
        if not hasattr(self, "assistant_tm_until_year_var") or not hasattr(self, "assistant_tm_until_day_var"):
            return
        fallback = float(self.assistant_data.get("training_match_until_age", self.assistant_data.get("start_age", 15.0)))
        value = self._assistant_age_value_from_vars(self.assistant_tm_until_year_var, self.assistant_tm_until_day_var, fallback=fallback)
        start_age = float(self.assistant_data.get("start_age", 15.0))
        end_age = float(self.assistant_data.get("end_age", max(start_age, value)))
        value = max(start_age, min(end_age, value))
        self._assistant_tm_until_sync_lock = True
        try:
            self._set_age_part_vars(self.assistant_tm_until_year_var, self.assistant_tm_until_day_var, value)
        finally:
            self._assistant_tm_until_sync_lock = False

    def _assistant_validate_intensity_fraction(self):
        if getattr(self, "assistant_intensity_fraction_lock", False):
            return

        if not hasattr(self, "assistant_intensity_boost_uses_var") or not hasattr(self, "assistant_intensity_boost_period_var"):
            return

        try:
            uses = int(float(str(self.assistant_intensity_boost_uses_var.get()).replace(",", ".")))
        except Exception:
            return

        try:
            period = int(float(str(self.assistant_intensity_boost_period_var.get()).replace(",", ".")))
        except Exception:
            return

        period = max(1, min(365, period))
        uses = max(1, min(period, uses))

        self.assistant_intensity_fraction_lock = True
        try:
            self.assistant_intensity_boost_period_var.set(str(period))
            self.assistant_intensity_boost_uses_var.set(str(uses))

            if hasattr(self, "assistant_intensity_boost_uses_spinbox"):
                self.assistant_intensity_boost_uses_spinbox.config(to=period)
        finally:
            self.assistant_intensity_fraction_lock = False

    def _assistant_toggle_intensity_controls(self):
        self._assistant_validate_intensity_fraction()
        enabled = bool(self.assistant_allow_intensity_var.get()) if hasattr(self, "assistant_allow_intensity_var") else False
        state = "normal" if enabled else "disabled"

        for widget_name in (
            "assistant_intensity_boost_uses_spinbox",
            "assistant_intensity_boost_period_spinbox",
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.config(state=state)

        fg = "#333333" if enabled else "#888888"
        for widget_name in (
            "assistant_intensity_slash_label",
            "assistant_intensity_days_label",
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                try:
                    widget.configure(foreground=fg)
                except Exception:
                    pass

    def _assistant_weight_changed(self, changed_stat):
        if getattr(self, "assistant_weight_lock", False):
            return

        if not hasattr(self, "assistant_weight_vars") or not hasattr(self, "assistant_weight_last_values"):
            return

        stats = list(self.assistant_weight_vars.keys())
        if changed_stat not in stats or len(stats) <= 1:
            return

        try:
            requested = float(str(self.assistant_weight_vars[changed_stat].get()).replace(",", "."))
        except Exception:
            return

        old_value = float(self.assistant_weight_last_values.get(changed_stat, requested))
        new_value = max(0.0, min(100.0, requested))
        delta = new_value - old_value

        if abs(delta) < 0.0001:
            return

        others = [stat for stat in stats if stat != changed_stat]
        values = {
            stat: float(self.assistant_weight_last_values.get(stat, 0.0))
            for stat in stats
        }
        values[changed_stat] = new_value

        if delta > 0:
            # Den ændrede egenskab er hævet. Træk forskellen ligeligt fra de øvrige.
            remaining = delta
            candidates = [stat for stat in others if values[stat] > 0.0]

            while remaining > 0.0001 and candidates:
                share = remaining / len(candidates)
                taken_total = 0.0
                next_candidates = []

                for stat in candidates:
                    take = min(values[stat], share)
                    values[stat] -= take
                    taken_total += take

                    if values[stat] > 0.0001:
                        next_candidates.append(stat)

                if taken_total <= 0.0001:
                    break

                remaining -= taken_total
                candidates = next_candidates

            if remaining > 0.0001:
                values[changed_stat] = max(0.0, values[changed_stat] - remaining)

        else:
            # Den ændrede egenskab er sænket. Fordel det frigivne ligeligt på de øvrige.
            remaining = -delta
            candidates = [stat for stat in others if values[stat] < 100.0]

            while remaining > 0.0001 and candidates:
                share = remaining / len(candidates)
                added_total = 0.0
                next_candidates = []

                for stat in candidates:
                    room = 100.0 - values[stat]
                    add = min(room, share)
                    values[stat] += add
                    added_total += add

                    if values[stat] < 99.9999:
                        next_candidates.append(stat)

                if added_total <= 0.0001:
                    break

                remaining -= added_total
                candidates = next_candidates

            if remaining > 0.0001:
                values[changed_stat] = min(100.0, values[changed_stat] + remaining)

        self._assistant_set_weight_values(values)

    def _assistant_set_weight_values(self, values):
        if not hasattr(self, "assistant_weight_vars"):
            return

        stats = list(self.assistant_weight_vars.keys())
        if not stats:
            return

        cleaned = {
            stat: max(0.0, min(100.0, float(values.get(stat, 0.0))))
            for stat in stats
        }

        total = sum(cleaned.values())
        if total <= 0:
            cleaned = {stat: 100.0 / len(stats) for stat in stats}
        else:
            cleaned = {stat: value * 100.0 / total for stat, value in cleaned.items()}

        rounded = {}
        running = 0.0

        for stat in stats[:-1]:
            value = round(cleaned[stat], 2)
            rounded[stat] = value
            running += value

        last_stat = stats[-1]
        rounded[last_stat] = round(100.0 - running, 2)

        if rounded[last_stat] < 0:
            deficit = -rounded[last_stat]
            rounded[last_stat] = 0.0
            for stat in reversed(stats[:-1]):
                take = min(rounded[stat], deficit)
                rounded[stat] -= take
                deficit -= take
                if deficit <= 0.0001:
                    break

        self.assistant_weight_lock = True
        try:
            for stat in stats:
                value = max(0.0, min(100.0, rounded[stat]))
                self.assistant_weight_last_values[stat] = value
                self.assistant_weight_vars[stat].set(f"{value:.2f}")
        finally:
            self.assistant_weight_lock = False


    def _assistant_update_weight_total(self):
        # Totalen vises ikke i UI. Vægtningerne normaliseres automatisk til 100%.
        return


    def _render_assistant_search(self):
        frame = ttk.Frame(self.assistant_body)
        frame.pack(fill="both", expand=True)

        setup = ttk.LabelFrame(frame, text="Søgestrategi", padding=10)
        setup.pack(fill="x", pady=(0, 10))
        setup.columnconfigure(1, weight=1)
        setup.columnconfigure(3, weight=0)

        ttk.Label(setup, text="Metode").grid(row=0, column=0, sticky="w")
        current_method = self.assistant_data.get("search_method", "Beam Search")
        if current_method == "Genetisk algoritme":
            current_method = "Genetic Algorithm"
            self.assistant_data["search_method"] = current_method
        self.assistant_search_method_var = tk.StringVar(value=current_method)
        self.assistant_search_method_box = self._create_fixed_dropdown(
            setup,
            self.assistant_search_method_var,
            values=["Beam Search", "Genetic Algorithm", "Simulated Annealing", "Monte Carlo", "Hybrid: Beam + Mutation", "Queen Seeker", "TM17 + Queen Seeker", "Brute Force"],
            width_px=240,
        )
        self.assistant_search_method_box.grid(row=0, column=1, sticky="w", padx=(8, 24))
        self.assistant_search_method_box.bind("<<ComboboxSelected>>", self._assistant_on_search_method_change)

        ttk.Label(setup, text="Søgetid").grid(row=0, column=2, sticky="w")
        self.assistant_search_time_var = tk.StringVar(value=display_search_time(self.assistant_data.get("search_time", "5 min"), self._language_code()))
        self.assistant_search_time_box = self._create_fixed_dropdown(
            setup,
            self.assistant_search_time_var,
            values=self._search_time_options(),
            width_px=120,
        )
        self.assistant_search_time_box.grid(row=0, column=3, sticky="w", padx=(8, 0))
        self._assistant_on_search_method_change()

        # XP-variation ligger nu som højreklikshandling på et enkelt resultat.
        # Den gamle globale checkbox fjernes fra UI, så almindelige søgninger ikke skjult får variation.
        self.assistant_xp_variance_var = tk.BooleanVar(value=False)
        self.assistant_data["xp_variance"] = False

        button_row = ttk.Frame(setup)
        button_row.grid(row=1, column=3, sticky="e", pady=(8, 0))
        self.assistant_stop_sim_button = ttk.Button(
            button_row,
            text="Start simulator",
            command=self._assistant_toggle_simulator_search,
            state="normal",
        )
        self.assistant_stop_sim_button.pack(side="right")

        self.assistant_search_status_var = tk.StringVar(value="Klar. Nye søgninger lægges oveni de gamle resultater.")
        ttk.Label(setup, textvariable=self.assistant_search_status_var, foreground="#666666").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(10, 0)
        )

        self.assistant_search_progress = ttk.Progressbar(setup, maximum=100.0, mode="determinate")
        self.assistant_search_progress.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))

        top = ttk.LabelFrame(frame, text="Søgeresultater", padding=10)
        top.pack(fill="both", expand=True)
        top.rowconfigure(0, weight=1)
        top.columnconfigure(0, weight=1)

        show_uw_rating = self._assistant_has_custom_weights()
        columns = ("rank", "score", "uw_score", "avg", "method", "notes") if show_uw_rating else ("rank", "score", "avg", "method", "notes")
        self.assistant_top_tree = ttk.Treeview(top, columns=columns, show="headings", height=12, selectmode="browse")
        self.assistant_top_tree_show_uw_rating = show_uw_rating

        heading_labels = [
            ("rank", "#"),
            ("score", "VT"),
        ]
        if show_uw_rating:
            heading_labels.append(("uw_score", "bVT"))
        heading_labels.extend([
            ("avg", "Gns."),
            ("method", "Metode"),
            ("notes", "Sessions"),
        ])
        for col, label in heading_labels:
            self._tree_heading_text_keys[(str(self.assistant_top_tree), col)] = label
            self.assistant_top_tree.heading(col, text=self._tr(label))

        self.assistant_top_tree.column("rank", width=42, anchor="center", stretch=False)
        self.assistant_top_tree.column("score", width=72, anchor="center", stretch=False)
        if show_uw_rating:
            self.assistant_top_tree.column("uw_score", width=86, anchor="center", stretch=False)
        self.assistant_top_tree.column("avg", width=72, anchor="center", stretch=False)
        self.assistant_top_tree.column("method", width=210, stretch=False)
        # 1.87: Program-kolonnen skal optage al resterende bredde,
        # så der ikke opstår en hvid restflade i højre side.
        self.assistant_top_tree.column("notes", width=630, minwidth=240, stretch=True)
        self.assistant_top_tree.grid(row=0, column=0, sticky="nsew")
        self.assistant_top_tree.bind("<Configure>", self._assistant_resize_results_columns)

        scroll = ttk.Scrollbar(top, orient="vertical", command=self.assistant_top_tree.yview)
        self.assistant_top_tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")

        self.assistant_result_menu = tk.Menu(self.assistant_window or self, tearoff=0)
        self.assistant_result_menu.add_command(label=self._tr("Importer til kontrolcenter"), command=self._assistant_import_selected_result)
        self.assistant_result_menu.add_separator()
        self.assistant_result_export_submenu = tk.Menu(self.assistant_result_menu, tearoff=0)
        self.assistant_result_export_submenu.add_command(label=self._tr("Rapport som PDF"), command=self._assistant_export_selected_report_pdf)
        self.assistant_result_export_submenu.add_command(label=self._tr("Rapport som DOC"), command=self._assistant_export_selected_report_doc)
        self.assistant_result_export_submenu.add_command(label=self._tr("Historik som CSV"), command=self._assistant_export_selected_history_csv)
        self.assistant_result_menu.add_cascade(label=self._tr("Eksportér"), menu=self.assistant_result_export_submenu)
        self.assistant_result_menu.add_separator()
        self.assistant_result_menu.add_command(label=self._tr("Gensimulér med XP-variation"), command=self._assistant_open_selected_xp_variation_window)
        self.assistant_result_menu.add_separator()

        self.assistant_result_search_submenu = tk.Menu(self.assistant_result_menu, tearoff=0)
        for method in ["Beam Search", "Genetic Algorithm", "Simulated Annealing", "Monte Carlo", "Hybrid: Beam + Mutation", "Queen Seeker", "Brute Force"]:
            self.assistant_result_search_submenu.add_command(
                label=method,
                command=lambda method=method: self._assistant_search_from_selected_result(method),
            )
        self.assistant_result_menu.add_cascade(label=self._tr("Søg videre med"), menu=self.assistant_result_search_submenu)

        self.assistant_top_tree.bind("<Button-3>", self._assistant_show_result_context_menu)
        self.assistant_top_tree.bind("<Button-2>", self._assistant_show_result_context_menu)
        self.assistant_top_tree.bind("<Double-1>", lambda _event: self._assistant_import_selected_result())
        self.assistant_top_tree.bind("<<TreeviewSelect>>", self._assistant_result_selection_changed_for_group_report, add="+")

        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text=self._tr("Importer valgte til kontrolcenter"), command=self._assistant_import_selected_result).pack(side="left")
        ttk.Button(actions, text=self._tr("Ryd søgning"), command=self._assistant_clear_search_from_button).pack(side="left", padx=(8, 0))
        self.assistant_export_button = ttk.Menubutton(actions, text="Eksportér")
        self.assistant_export_button_menu = tk.Menu(self.assistant_export_button, tearoff=0)
        self.assistant_export_button_menu.add_command(label=self._tr("Rapport som PDF"), command=self._assistant_export_selected_report_pdf)
        self.assistant_export_button_menu.add_command(label=self._tr("Rapport som DOC"), command=self._assistant_export_selected_report_doc)
        self.assistant_export_button_menu.add_command(label=self._tr("Historik som CSV"), command=self._assistant_export_selected_history_csv)
        self.assistant_export_button["menu"] = self.assistant_export_button_menu
        self.assistant_export_button.pack(side="left", padx=(8, 0))
        ttk.Label(actions, text="Højreklik på et resultat for at søge videre.", foreground="#666666").pack(side="right")

        self._assistant_fill_top_results(self._assistant_all_results())

    def _assistant_all_results(self):
        raw_results = list(self.assistant_data.get("search_results_pool") or self.assistant_data.get("top_results") or [])
        results = []
        changed = False
        for item in raw_results:
            if isinstance(item, dict):
                try:
                    results.append(self._assistant_result_from_dict(item))
                    changed = True
                except Exception:
                    continue
            else:
                results.append(item)
        if changed:
            try:
                self.assistant_data["search_results_pool"] = results
                self.assistant_data["top_results"] = results[:10]
            except Exception:
                pass
        return results

    def _assistant_program_signature(self, program):
        signature = []
        for phase in (program or []):
            if isinstance(phase, TrainingCycle):
                signature.append((
                    "cycle",
                    str(phase.name),
                    int(phase.repetitions),
                    tuple(
                        (
                            int(sub.days),
                            str(sub.exercise),
                            int(getattr(sub, "training_points", 23)),
                            tuple(sorted((sub.distribution or {}).items())),
                        )
                        for sub in phase.phases
                    ),
                ))
            else:
                signature.append((
                    int(phase.days),
                    str(phase.exercise),
                    int(getattr(phase, "training_points", 23)),
                    tuple(sorted((phase.distribution or {}).items())),
                ))
        return tuple(signature)

    def _assistant_result_signature(self, result):
        return self._assistant_program_signature(result.program)


    def _assistant_normalized_weights_for_position(self, position, weights=None):
        position = internal_position(position)
        stats = POSITION_STATS[position]
        source = weights if weights is not None else POSITION_WEIGHTS[position]
        cleaned = {}
        for stat in stats:
            try:
                cleaned[stat] = max(0.0, float((source or {}).get(stat, 0.0)))
            except Exception:
                cleaned[stat] = 0.0
        total = sum(cleaned.values())
        if total <= 0:
            return {stat: 100.0 / len(stats) for stat in stats}
        return {stat: value * 100.0 / total for stat, value in cleaned.items()}

    def _assistant_has_custom_weights(self):
        try:
            position = internal_position(self.assistant_data.get("position", self.position_var.get()))
        except Exception:
            position = "Keepere"
        current = self._assistant_normalized_weights_for_position(position, self.assistant_data.get("weights", POSITION_WEIGHTS[position]))
        default = self._assistant_normalized_weights_for_position(position, POSITION_WEIGHTS[position])
        for stat in POSITION_STATS[position]:
            if abs(float(current.get(stat, 0.0)) - float(default.get(stat, 0.0))) > 0.01:
                return True
        return False

    def _assistant_result_uw_rating(self, result):
        value = getattr(result, "uw_rating", None)
        if value is not None:
            try:
                return float(value)
            except Exception:
                pass
        try:
            position = internal_position(self.assistant_data.get("position", self.position_var.get()))
            weights = self._assistant_normalized_weights_for_position(position, self.assistant_data.get("weights", POSITION_WEIGHTS[position]))
            final_stats = dict(getattr(result, "final_stats", {}) or {})
            total = sum(weights.values()) or 1.0
            return sum(float(final_stats.get(stat, 0.0)) * weight for stat, weight in weights.items()) / total
        except Exception:
            return float(getattr(result, "rating", 0.0) or 0.0)

    def _assistant_primary_rating_for_display(self, result):
        if self._assistant_has_custom_weights():
            return self._assistant_result_uw_rating(result)
        return float(getattr(result, "rating", 0.0) or 0.0)

    def _assistant_result_sort_key(self, result):
        """Visible ordering: highest VT first, then highest average.

        Internal optimizer score may include penalties/bonuses, but it should not make
        the table look unsorted to the user.
        """
        try:
            rating = float(getattr(result, "rating", 0.0))
        except Exception:
            rating = 0.0
        try:
            uw_rating = float(self._assistant_result_uw_rating(result))
        except Exception:
            uw_rating = rating
        try:
            average = float(getattr(result, "average", 0.0))
        except Exception:
            average = 0.0
        try:
            score = float(getattr(result, "score", uw_rating if self._assistant_has_custom_weights() else rating))
        except Exception:
            score = uw_rating if self._assistant_has_custom_weights() else rating
        primary = uw_rating if self._assistant_has_custom_weights() else rating
        return (-primary, -rating, -average, -score)

    def _assistant_dedupe_and_rank_results(self, results, limit=None):
        unique = {}
        for result in results or []:
            if result is None:
                continue
            sig = self._assistant_result_signature(result)
            old = unique.get(sig)
            if old is None or self._assistant_result_sort_key(result) < self._assistant_result_sort_key(old):
                unique[sig] = result

        ranked = sorted(unique.values(), key=self._assistant_result_sort_key)
        if limit is not None:
            ranked = ranked[:limit]
        for idx, result in enumerate(ranked, start=1):
            result.rank = idx
        return ranked

    def _assistant_merge_search_results(self, new_results):
        pool = list(self.assistant_data.get("search_results_pool") or [])
        pool.extend(new_results or [])
        pool = self._assistant_dedupe_and_rank_results(pool, limit=200)
        self.assistant_data["search_results_pool"] = pool
        self.assistant_data["top_results"] = pool[:10]
        return pool

    def _assistant_fill_top_results(self, results):
        if not hasattr(self, "assistant_top_tree"):
            return

        for item in self.assistant_top_tree.get_children():
            self.assistant_top_tree.delete(item)

        self.assistant_tree_result_map = {}
        results = self._assistant_dedupe_and_rank_results(results or [], limit=100)

        if not results:
            empty_values = ["—", "—"]
            if getattr(self, "assistant_top_tree_show_uw_rating", False):
                empty_values.append("—")
            empty_values.extend(["—", "—", self._tr("ingen resultater endnu")])
            self.assistant_top_tree.insert("", "end", values=tuple(empty_values))
            return

        for index, result in enumerate(results, start=1):
            iid = f"result_{index}"
            self.assistant_tree_result_map[iid] = result
            values = [
                index,
                f"{result.rating:.2f}",
            ]
            if getattr(self, "assistant_top_tree_show_uw_rating", False):
                values.append(f"{self._assistant_result_uw_rating(result):.2f}")
            values.extend([
                f"{result.average:.2f}",
                self._display_domain_text(result.method),
                self._display_domain_text(result.notes),
            ])
            self.assistant_top_tree.insert(
                "",
                "end",
                iid=iid,
                values=tuple(values),
            )

    def _assistant_resize_results_columns(self, event=None):
        """Lad Program-kolonnen fylde resten af resultattabellen."""
        tree = getattr(self, "assistant_top_tree", None)
        if tree is None:
            return

        try:
            total_width = int(event.width if event is not None else tree.winfo_width())
        except Exception:
            return

        fixed_width = 42 + 72 + 72 + 210
        if getattr(self, "assistant_top_tree_show_uw_rating", False):
            fixed_width += 86
        # Lidt luft til borders/scrollbar internt i Tk.
        program_width = max(240, total_width - fixed_width - 12)

        try:
            tree.column("rank", width=42, stretch=False)
            tree.column("score", width=72, stretch=False)
            if getattr(self, "assistant_top_tree_show_uw_rating", False):
                tree.column("uw_score", width=86, stretch=False)
            tree.column("avg", width=72, stretch=False)
            tree.column("method", width=210, stretch=False)
            tree.column("notes", width=program_width, stretch=True)
        except Exception:
            pass


    def _assistant_result_selection_changed_for_group_report(self, event=None):
        try:
            if getattr(self, "active_group", None):
                self._schedule_group_report_recompute(delay=180)
        except Exception:
            pass

    def _assistant_selected_result(self):
        if not hasattr(self, "assistant_top_tree"):
            raise ValueError("Der er ingen søgeresultater endnu.")
        selection = self.assistant_top_tree.selection()
        if not selection:
            raise ValueError("Vælg først et træningsprogram i søgeresultatet.")
        result = getattr(self, "assistant_tree_result_map", {}).get(selection[0])
        if result is None:
            raise ValueError("Det valgte søgeresultat kan ikke bruges.")
        return result

    def _assistant_show_result_context_menu(self, event):
        if not hasattr(self, "assistant_top_tree"):
            return
        iid = self.assistant_top_tree.identify_row(event.y)
        if iid:
            self.assistant_top_tree.selection_set(iid)
            self.assistant_top_tree.focus(iid)
        try:
            self.assistant_result_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.assistant_result_menu.grab_release()

    def _assistant_result_to_dict(self, result):
        return {
            "rank": int(result.rank),
            "score": float(result.score),
            "rating": float(result.rating),
            "uw_rating": None if getattr(result, "uw_rating", None) is None else float(getattr(result, "uw_rating")),
            "average": float(result.average),
            "days": int(result.days),
            "method": result.method,
            "notes": result.notes,
            "final_stats": dict(result.final_stats),
            "program": [self._serialize_program_item(phase) for phase in result.program],
        }

    def _assistant_result_from_dict(self, data):
        program = [self._deserialize_program_item(item) for item in data.get("program", [])]
        return AssistantPlanResult(
            rank=int(data.get("rank", 0)),
            score=float(data.get("score", data.get("rating", 0.0))),
            rating=float(data.get("rating", data.get("score", 0.0))),
            uw_rating=None if data.get("uw_rating", None) is None else float(data.get("uw_rating")),
            average=float(data.get("average", 0.0)),
            days=int(data.get("days", sum(item.days for item in program))),
            method=str(data.get("method", "Indlæst")),
            notes=str(data.get("notes", "")),
            program=program,
            final_stats=dict(data.get("final_stats", {})),
        )

    def _assistant_serializable_search_data(self):
        self._assistant_commit_current_step()
        data = copy.deepcopy(self.assistant_data)
        pool = self._assistant_all_results()
        data["search_results_pool"] = [self._assistant_result_to_dict(result) for result in pool]
        data["top_results"] = [self._assistant_result_to_dict(result) for result in pool[:10]]
        data["xp"] = self.xp_var.get()
        data["xp_reference_intensity"] = internal_intensity(self.xp_reference_intensity_var.get())
        return {"format": "VMAN Training Planner Assistent Search", "version": "2.24", "assistant_data": data}

    def _assistant_save_search(self):
        try:
            parent = self.assistant_window if self.assistant_window is not None and self.assistant_window.winfo_exists() else self
            # 1.90: Samme macOS/Tk-fix som Indlæs.
            # Undgå filetypes/defaultextension i native dialogen, da Tk på visse
            # macOS/Python-builds kan abort-crashe i allowed-file-types-laget.
            path = filedialog.asksaveasfilename(
                parent=parent,
                title="Gem hele Assistent-søgningen",
                initialfile="assistent_søgning.vmansearch.json",
            )
            if not path:
                return

            path_obj = Path(path)
            if not path_obj.name.endswith(".vmansearch.json"):
                if path_obj.suffix:
                    path_obj = path_obj.with_suffix(path_obj.suffix + ".vmansearch.json")
                else:
                    path_obj = path_obj.with_name(path_obj.name + ".vmansearch.json")

            if path_obj.exists() and not self._confirm_overwrite_action("Overskrivning af gemt Assistent-søgning", parent=parent):
                return

            with open(path_obj, "w", encoding="utf-8") as f:
                json.dump(self._assistant_serializable_search_data(), f, ensure_ascii=False, indent=2)
            if hasattr(self, "assistant_search_status_var"):
                self.assistant_search_status_var.set(f"Søgning gemt: {path_obj.name}")
        except Exception as e:
            self._assistant_handle_error(e)

    def _assistant_load_search(self):
        """Indlæs en gemt Assistent-søgning uden at kunne lukke/crashe appen.

        1.88: Fil-dialogen får eksplicit parent, data normaliseres mod nye
        standardfelter, og side 3 re-renderes før tabellen udfyldes. Det undgår
        at en gammel/stale Treeview eller ældre .vmansearch-fil kan vælte UI'et.
        """
        path = None
        try:
            parent = self.assistant_window if self.assistant_window is not None and self.assistant_window.winfo_exists() else self
            # 1.89: Undgå filetypes på macOS/Tk.
            # På nogle Tk 8.6/Conda/Python 3.13 builds kan native macOS-
            # filvælgeren abort-crashe i setAllowedFileTypes før Python kan
            # fange fejlen. Derfor filtrerer vi ikke i dialogen, men validerer
            # JSON-formatet efter filvalg.
            path = filedialog.askopenfilename(
                parent=parent,
                title="Indlæs Assistent-søgning",
            )
            if not path:
                return

            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except json.JSONDecodeError as error:
                raise ValueError("Filen er ikke en gyldig VMAN/JSON-søgning.") from error

            if not isinstance(raw, dict):
                raise ValueError("Filen ligner ikke en VMAN Assistent-søgning.")

            raw_data = raw.get("assistant_data", raw)
            if not isinstance(raw_data, dict):
                raise ValueError("Filen mangler gyldige Assistent-data.")

            # Start med aktuelle standarder, så ældre søgefiler får nye felter
            # som Fast rul, Træningskamp-indledning osv.
            data = self._assistant_snapshot_from_ui()
            data.update(raw_data)

            try:
                data["position"] = internal_position(data.get("position", self.position_var.get()))
            except Exception:
                data["position"] = internal_position(self.position_var.get())

            if data.get("position") not in POSITION_STATS:
                data["position"] = internal_position(self.position_var.get())

            if not isinstance(data.get("weights"), dict):
                data["weights"] = dict(POSITION_WEIGHTS[data["position"]])

            source_results = data.get("search_results_pool", data.get("top_results", []))
            if not isinstance(source_results, list):
                source_results = []

            pool = []
            bad_results = 0
            for item in source_results:
                try:
                    pool.append(self._assistant_result_from_dict(item))
                except Exception:
                    bad_results += 1

            data["search_results_pool"] = self._assistant_dedupe_and_rank_results(pool, limit=200)
            data["top_results"] = data["search_results_pool"][:10]
            data["_search_has_run"] = bool(data["search_results_pool"])
            data["_assistant_workspace_locked"] = True
            data["_assistant_reopen_step"] = 2

            self.assistant_data = data
            self.assistant_step = 2
            self._render_assistant_step()

            if hasattr(self, "assistant_top_tree"):
                self._assistant_fill_top_results(self._assistant_all_results())
                self._assistant_resize_results_columns()

            if hasattr(self, "assistant_search_status_var"):
                extra = f" · sprang {bad_results} ugyldige resultater over" if bad_results else ""
                self.assistant_search_status_var.set(f"Søgning indlæst: {Path(path).name}{extra}")

        except Exception as e:
            # Undgå at en fejl i fejl-dialogen selv kan lukke appen.
            try:
                self._assistant_handle_error(e)
            except Exception:
                try:
                    messagebox.showerror(self._tr("Assistent-fejl"), f"{self._tr("Kunne ikke indlæse søgningen.")}\n\n{e}", parent=self)
                except Exception:
                    pass

    def _assistant_get_search_xp(self):
        try:
            return float(str(self.assistant_data.get("xp", self.xp_var.get())).replace(",", "."))
        except Exception:
            try:
                return float(str(self.xp_var.get()).replace(",", "."))
            except Exception:
                return 205.0

    def _assistant_apply_result_to_main(self, result, simulate_after=True):
        self._push_undo("Importer assistentresultat")
        previous_import_flag = bool(
            getattr(self, "_assistant_result_import_in_progress", False)
        )
        self._assistant_result_import_in_progress = True
        try:
            snapshot = self.assistant_data
            position = internal_position(
                snapshot.get("position", self.position_var.get())
            )
            self.position_var.set(self._display_position(position))
            self.current_stats = POSITION_STATS[position]
            self._render_start_stats()

            self._set_main_start_age_decimal(snapshot.get("start_age", "15.0"))
            self.xp_var.set(str(snapshot.get("xp", self.xp_var.get())))
            self.xp_reference_intensity_var.set(
                self._display_intensity(
                    internal_intensity(
                        snapshot.get(
                            "xp_reference_intensity",
                            self.xp_reference_intensity_var.get(),
                        )
                    )
                )
            )

            ebr_enabled = bool(snapshot.get("ebr_enabled", False))
            self.ebr_enabled_var.set(ebr_enabled)
            self.ebr_ratio_var.set(str(snapshot.get("ebr_ratio", "1.10")))
            self.ebr_knee_var.set(str(snapshot.get("ebr_knee", "linear")))
            self._on_ebr_toggle()

            start_stats = snapshot.get("start_stats", {})
            for stat in POSITION_STATS[position]:
                try:
                    start_value = int(round(float(start_stats.get(stat, 2.0))))
                except Exception:
                    start_value = 2
                self.start_stat_vars[stat].set(
                    str(max(0, min(100, start_value)))
                )

            self._refresh_session_exercise_options(
                position, reset_if_invalid=False
            )
            self.program = copy.deepcopy(result.program)
            self.editing_index = None
            self.active_position = position
            self._refresh_program_tree()
            self._set_template_name(
                f"Assistent {result.rank} - {result.rating:.2f}", dirty=True
            )
            self._settings_changed("program")
            if simulate_after:
                self._simulate()
        finally:
            self._assistant_result_import_in_progress = previous_import_flag

    def _assistant_import_selected_result(self):
        try:
            result = self._assistant_selected_result()
            self._assistant_apply_result_to_main(result, simulate_after=True)
            if hasattr(self, "assistant_search_status_var"):
                self.assistant_search_status_var.set(f"Importerede resultat #{result.rank} til kontrolcenter.")
        except Exception as e:
            self._assistant_handle_error(e)

    def _assistant_simulate_result(self, result):
        snapshot = self.assistant_data
        position = internal_position(snapshot.get("position", self.position_var.get()))
        start_stats_raw = snapshot.get("start_stats", {})
        start_stats = {}
        for stat in POSITION_STATS[position]:
            try:
                start_stats[stat] = max(0, min(100, int(round(float(start_stats_raw.get(stat, 0))))))
            except Exception:
                start_stats[stat] = 0
        start_age = float(snapshot.get("start_age", 15.0))
        final_state, history = simulate_program(
            start_stats=start_stats,
            program=result.program,
            position=position,
            xp=self._assistant_get_search_xp(),
            start_age=start_age,
            keep_history=True,
            reference_training_points=INTENSITY_OPTIONS.get(internal_intensity(snapshot.get("xp_reference_intensity", self.xp_reference_intensity_var.get())), self._get_xp_reference_training_points()),
            experience_bonus_enabled=bool(snapshot.get("ebr_enabled", False)),
            experience_bonus_ratio=float(snapshot.get("ebr_ratio", 1.10)),
            experience_bonus_knee=snapshot.get("ebr_knee", "linear"),
            experience_bonus_start_age=start_age,
        )
        return position, start_age, summarize_state(final_state, position=position), history

    def _assistant_build_result_report_lines(self, result, summary):
        snapshot = self.assistant_data
        position = internal_position(snapshot.get("position", self.position_var.get()))
        display_pos = self._display_position(position)
        start_age = float(snapshot.get("start_age", 15.0))
        total_days = sum(phase.days for phase in result.program)
        end_age = calculate_end_age(start_age, total_days)
        lines = []
        lines.append("VMAN Training Planner - Assistent søgerapport")
        lines.append("=" * 38)
        lines.append("")
        lines.append(f"Resultat: #{result.rank}")
        lines.append(f"Metode: {result.method}")
        lines.append(f"Position: {display_pos}")
        lines.append(f"Periode: {start_age:.2f} -> {end_age:.2f}")
        lines.append(f"Programdage: {total_days}")
        lines.append(f"XP: {self._assistant_get_search_xp():.2f}")
        lines.append(f"XP ved: {self._display_intensity(internal_intensity(snapshot.get('xp_reference_intensity', self.xp_reference_intensity_var.get())))}")
        if bool(snapshot.get("ebr_enabled", False)):
            knee_label = {"early": "tidlig", "linear": "lineær", "late": "sen"}.get(snapshot.get("ebr_knee", "linear"), "lineær")
            lines.append(f"Erfaringsbonus: aktiv - ratio {float(snapshot.get('ebr_ratio', 1.10)):.2f} - gradient {knee_label}")
        else:
            lines.append("Erfaringsbonus: slået fra")
        lines.append("")
        lines.append("Træningsprogram")
        lines.append("-" * 15)
        for i, phase in enumerate(result.program, start=1):
            lines.extend(self._program_item_to_report_lines(phase, i))
            lines.append("")
        lines.append("")
        lines.append("Slutstats")
        lines.append("-" * 9)
        for stat in POSITION_STATS[position]:
            lines.append(f"{stat}: {summary[stat]:.2f}")
        lines.append("")
        lines.append(f"Gennemsnit: {summary['Gennemsnit']:.2f}")
        lines.append(f"Vurderingstal ({display_pos}): {summary[f'Vurderingstal_{position}']:.2f}")
        return lines

    def _assistant_export_selected_report_pdf(self):
        try:
            result = self._assistant_selected_result()
            _position, _start_age, summary, _history = self._assistant_simulate_result(result)
            lines = self._assistant_build_result_report_lines(result, summary)
            path = filedialog.asksaveasfilename(
                defaultextension=".pdf",
                filetypes=[("PDF", "*.pdf")],
                title="Eksportér valgt Assistent-rapport som PDF",
            )
            if not path:
                return
            self._write_simple_pdf(path, lines)
        except Exception as e:
            self._assistant_handle_error(e)

    def _assistant_export_selected_report_doc(self):
        try:
            result = self._assistant_selected_result()
            _position, _start_age, summary, _history = self._assistant_simulate_result(result)
            lines = self._assistant_build_result_report_lines(result, summary)
            path = filedialog.asksaveasfilename(
                defaultextension=".doc",
                filetypes=[("Word-kompatibel DOC", "*.doc"), ("RTF", "*.rtf")],
                title="Eksportér valgt Assistent-rapport som DOC",
            )
            if not path:
                return
            rtf_lines = [r"{\rtf1\ansi\deff0", r"{\fonttbl{\f0 Helvetica;}}", r"\fs22"]
            for line in lines:
                if line.startswith("VMAN Training Planner"):
                    rtf_lines.append(r"\b " + self._rtf_escape(line) + r"\b0\par")
                elif line in {"Træningsprogram", "Slutstats"}:
                    rtf_lines.append(r"\par\b " + self._rtf_escape(line) + r"\b0\par")
                elif set(line) <= {"=", "-"} and line:
                    continue
                elif line == "":
                    rtf_lines.append(r"\par")
                else:
                    rtf_lines.append(self._rtf_escape(line) + r"\par")
            rtf_lines.append("}")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(rtf_lines))
        except Exception as e:
            self._assistant_handle_error(e)

    def _write_history_csv_to_path(self, path, history, position):
        stats = POSITION_STATS[position]
        fieldnames = ["day", "age", "exercise", "intensity", "rating", "average"] + [f"stat_{s}" for s in stats]
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for result in history:
                intensity = INTENSITY_LABELS_BY_POINTS.get(result.training_points, str(result.training_points))
                average = sum(result.after_stats[stat] for stat in stats) / len(stats)
                row = {
                    "day": int(result.day),
                    "age": self._csv_number(result.age),
                    "exercise": result.exercise,
                    "intensity": intensity,
                    "rating": "" if result.rating is None else self._csv_number(result.rating),
                    "average": self._csv_number(average),
                }
                for stat in stats:
                    row[f"stat_{stat}"] = self._csv_number(result.after_stats[stat])
                writer.writerow(row)

    def _assistant_export_selected_history_csv(self):
        try:
            result = self._assistant_selected_result()
            position, _start_age, _summary, history = self._assistant_simulate_result(result)
            path = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV", "*.csv")],
                title="Eksportér valgt Assistent-historik",
            )
            if not path:
                return
            self._write_history_csv_to_path(path, history, position)
        except Exception as e:
            self._assistant_handle_error(e)


    def _assistant_open_selected_xp_variation_window(self):
        """Analysér hvor robust det valgte grundprogram er ved XP-variation.

        1.74: Vinduet er en ren følsomhedsanalyse. Det samme program
        simuleres 100 gange med forskellige XP-niveauer; resultatet opdeles i
        de 10 bedste og 10 værste udfald. Ingen enkeltvariation kan importeres.
        """
        try:
            seed_result = self._assistant_selected_result()
        except Exception as e:
            self._assistant_handle_error(e)
            return

        snapshot = self.assistant_data
        position = internal_position(snapshot.get("position", self.position_var.get()))
        display_pos = self._display_position(position)
        raw_start_stats = snapshot.get("start_stats", self._get_start_stats())
        start_stats = {}
        for stat in POSITION_STATS[position]:
            try:
                start_stats[stat] = int(round(float(raw_start_stats.get(stat, 2))))
            except Exception:
                start_stats[stat] = 2
        start_age = float(snapshot.get("start_age", 15.0))
        base_xp = self._assistant_get_search_xp()
        reference_points = INTENSITY_OPTIONS.get(
            internal_intensity(snapshot.get("xp_reference_intensity", self.xp_reference_intensity_var.get())),
            self._get_xp_reference_training_points(),
        )
        weights = snapshot.get("weights", POSITION_WEIGHTS[position])
        weight_total = sum(float(value) for value in weights.values()) or 1.0
        total_days = sum(phase.days for phase in seed_result.program)

        def weighted_rating(stats):
            return sum(float(stats.get(stat, 0.0)) * float(weights.get(stat, 0.0)) for stat in weights) / weight_total

        # Base bruges kun som reference for ΔVT. Variationstabellerne viser 100
        # gensimuleringer af samme program.
        base_state, _ = simulate_program(
            start_stats=start_stats,
            program=copy.deepcopy(seed_result.program),
            position=position,
            xp=base_xp,
            start_age=start_age,
            keep_history=False,
            reference_training_points=reference_points,
            experience_bonus_enabled=bool(snapshot.get("ebr_enabled", False)),
            experience_bonus_ratio=float(snapshot.get("ebr_ratio", 1.05)),
            experience_bonus_knee=snapshot.get("ebr_knee", "linear"),
            experience_bonus_start_age=start_age,
        )
        base_rating = weighted_rating(base_state.stat_values(mode="fractional"))

        seed_text = json.dumps(
            {
                "position": position,
                "rank": seed_result.rank,
                "rating": round(seed_result.rating, 4),
                "days": total_days,
                "base_xp": round(base_xp, 4),
                "program": [getattr(phase, "__dict__", str(phase)) for phase in seed_result.program],
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        rng = random.Random(seed_text)

        xp_values = [base_xp]
        while len(xp_values) < 100:
            # 1.79: Halveret variationsgrad. Tidligere var spændet ca. ±10 XP.
            # Nu bruges ca. ±5 XP med 100 udfald.
            xp_values.append(max(1.0, base_xp + rng.uniform(-5.0, 5.0)))

        rows = []
        for idx, xp_value in enumerate(xp_values, start=1):
            final_state, _history = simulate_program(
                start_stats=start_stats,
                program=copy.deepcopy(seed_result.program),
                position=position,
                xp=xp_value,
                start_age=start_age,
                keep_history=False,
                reference_training_points=reference_points,
                experience_bonus_enabled=bool(snapshot.get("ebr_enabled", False)),
                experience_bonus_ratio=float(snapshot.get("ebr_ratio", 1.05)),
                experience_bonus_knee=snapshot.get("ebr_knee", "linear"),
                experience_bonus_start_age=start_age,
            )
            stats = final_state.stat_values(mode="fractional")
            rating = weighted_rating(stats)
            average = sum(stats.get(stat, 0.0) for stat in POSITION_STATS[position]) / len(POSITION_STATS[position])
            rows.append({
                "sample": idx,
                "xp": xp_value,
                "rating": rating,
                "delta": rating - base_rating,
                "average": average,
            })

        best_rows = sorted(rows, key=lambda row: (row["rating"], row["average"]), reverse=True)[:10]
        worst_rows = sorted(rows, key=lambda row: (row["rating"], row["average"]))[:10]

        win = tk.Toplevel(self.assistant_window or self)
        win.title(self._tr("Gensimulér med XP-variation"))
        win.geometry("640x640")
        win.minsize(560, 560)
        win.transient(self.assistant_window or self)

        outer = ttk.Frame(win, padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        outer.rowconfigure(2, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text=f"XP-variationsanalyse · grundprogram #{seed_result.rank} · {seed_result.rating:.2f} VT",
            font=("", 12, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text=f"Samme program · 100 variationer · {display_pos} · {total_days} dage · base-XP {base_xp:.0f}",
            foreground="#666666",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))

        columns = ("rank", "xp", "rating", "delta", "avg")

        def build_section(parent, title, row_index):
            section = ttk.LabelFrame(parent, text=title, padding=8)
            section.grid(row=row_index, column=0, sticky="nsew", pady=(0, 8))
            section.rowconfigure(0, weight=1)
            section.columnconfigure(0, weight=1)
            tree = ttk.Treeview(section, columns=columns, show="headings", height=10, selectmode="browse")
            for col, label in [
                ("rank", "#"),
                ("xp", "XP"),
                ("rating", "VT"),
                ("delta", "ΔVT"),
                ("avg", "Gns."),
            ]:
                tree.heading(col, text=label)
            tree.column("rank", width=42, anchor="center", stretch=False)
            tree.column("xp", width=85, anchor="center", stretch=True)
            tree.column("rating", width=85, anchor="center", stretch=True)
            tree.column("delta", width=85, anchor="center", stretch=True)
            tree.column("avg", width=85, anchor="center", stretch=True)
            tree.grid(row=0, column=0, sticky="nsew")
            scroll = ttk.Scrollbar(section, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=scroll.set)
            scroll.grid(row=0, column=1, sticky="ns")
            return tree

        best_tree = build_section(outer, "10 bedste udfald", 1)
        worst_tree = build_section(outer, "10 værste udfald", 2)

        def signed(value):
            return f"{value:+.2f}"

        def fill_tree(tree, items):
            for idx, row in enumerate(items, start=1):
                tree.insert(
                    "",
                    "end",
                    values=(
                        idx,
                        f"{row['xp']:.1f}",
                        f"{row['rating']:.2f}",
                        signed(row["delta"]),
                        f"{row['average']:.2f}",
                    ),
                )

        fill_tree(best_tree, best_rows)
        fill_tree(worst_tree, worst_rows)

        bottom = ttk.Frame(outer)
        bottom.grid(row=3, column=0, sticky="ew", pady=(2, 0))
        bottom.columnconfigure(0, weight=1)
        min_vt = min(row["rating"] for row in rows)
        max_vt = max(row["rating"] for row in rows)
        status_var = tk.StringVar(value=f"Færdig · 100 variationer · VT-spænd {min_vt:.2f}–{max_vt:.2f} ({max_vt - min_vt:.2f})")
        ttk.Label(bottom, textvariable=status_var, foreground="#666666").pack(side="left")

        def build_report_lines():
            lines = []
            lines.append("VMAN Training Planner - XP-variationsrapport")
            lines.append("=" * 38)
            lines.append("")
            lines.append(f"Grundprogram: #{seed_result.rank}")
            lines.append(f"Position: {display_pos}")
            lines.append(f"Programdage: {total_days}")
            lines.append(f"Base-XP: {base_xp:.2f}")
            lines.append(f"XP ved: {self._display_intensity(internal_intensity(snapshot.get('xp_reference_intensity', self.xp_reference_intensity_var.get())))}")
            lines.append(f"Base-VT: {base_rating:.2f}")
            lines.append(f"Antal variationer: {len(rows)}")
            lines.append(f"VT-spænd: {min_vt:.2f} - {max_vt:.2f}")
            lines.append(f"Samlet følsomhed: {max_vt - min_vt:.2f} VT-point")
            lines.append("")
            lines.append("10 bedste udfald")
            lines.append("-" * 16)
            for idx, row in enumerate(best_rows, start=1):
                lines.append(f"{idx}. XP {row['xp']:.1f}, VT {row['rating']:.2f} ({signed(row['delta'])}), gns. {row['average']:.2f}")
            lines.append("")
            lines.append("10 værste udfald")
            lines.append("-" * 19)
            for idx, row in enumerate(worst_rows, start=1):
                lines.append(f"{idx}. XP {row['xp']:.1f}, VT {row['rating']:.2f} ({signed(row['delta'])}), gns. {row['average']:.2f}")
            lines.append("")
            lines.append("Alle 100 variationer")
            lines.append("-" * 19)
            for row in sorted(rows, key=lambda item: item["sample"]):
                lines.append(f"XP {row['xp']:.1f}, VT {row['rating']:.2f} ({signed(row['delta'])}), gns. {row['average']:.2f}")
            lines.append("")
            lines.append("Bemærkning: Programmet er identisk i alle variationer. Kun XP-niveauet ændres.")
            return lines

        def export_pdf():
            try:
                path = filedialog.asksaveasfilename(
                    defaultextension=".pdf",
                    filetypes=[("PDF", "*.pdf")],
                    title="Eksportér XP-variationsrapport som PDF",
                )
                if not path:
                    return
                self._write_simple_pdf(path, build_report_lines())
                status_var.set("PDF-rapport gemt.")
            except Exception as e:
                self._assistant_handle_error(e)

        def export_doc():
            try:
                path = filedialog.asksaveasfilename(
                    defaultextension=".doc",
                    filetypes=[("Word-kompatibel DOC", "*.doc"), ("RTF", "*.rtf")],
                    title="Eksportér XP-variationsrapport som DOC",
                )
                if not path:
                    return
                rtf_lines = [r"{\rtf1\ansi\deff0", r"{\fonttbl{\f0 Helvetica;}}", r"\fs22"]
                section_headers = {"10 bedste udfald", "10 værste udfald", "Alle 100 variationer"}
                for line in build_report_lines():
                    if line.startswith("VMAN Training Planner"):
                        rtf_lines.append(r"\b " + self._rtf_escape(line) + r"\b0\par")
                    elif line in section_headers:
                        rtf_lines.append(r"\par\b " + self._rtf_escape(line) + r"\b0\par")
                    elif set(line) <= {"=", "-"} and line:
                        continue
                    elif line == "":
                        rtf_lines.append(r"\par")
                    else:
                        rtf_lines.append(self._rtf_escape(line) + r"\par")
                rtf_lines.append("}")
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(rtf_lines))
                status_var.set(self._tr("DOC-rapport gemt."))
            except Exception as e:
                self._assistant_handle_error(e)

        # 1.81: Knapperne ligger i samme grid-række, så de flugter visuelt.
        button_row = ttk.Frame(bottom)
        button_row.grid(row=0, column=1, sticky="e")

        close_button = ttk.Button(button_row, text=self._tr("Luk"), command=win.destroy)
        close_button.grid(row=0, column=0, sticky="e", padx=(0, 8), pady=0)

        export_menu_button = ttk.Menubutton(button_row, text=self._tr("Eksportér rapport"))
        export_menu = tk.Menu(export_menu_button, tearoff=0)
        export_menu.add_command(label="PDF", command=export_pdf)
        export_menu.add_command(label="DOC", command=export_doc)
        export_menu_button["menu"] = export_menu
        export_menu_button.grid(row=0, column=1, sticky="e", pady=0)

    def _assistant_search_from_selected_result(self, method):
        try:
            if method == "TM17 + Queen Seeker":
                messagebox.showinfo(self._tr("Søg videre"), self._tr("TM17 + Queen Seeker kan kun vælges som ny søgning, ikke som 'søg videre' fra et eksisterende program."))
                return
            result = self._assistant_selected_result()
            self._assistant_run_simulator_search(seed_result=result, method_override=method)
        except Exception as e:
            self._assistant_handle_error(e)

    def _assistant_toggle_simulator_search(self):
        if getattr(self, "assistant_sim_running", False):
            self._assistant_stop_simulator_search()
        else:
            self._assistant_run_simulator_search()

    def _assistant_run_simulator_search(self, seed_result=None, method_override=None):
        self._assistant_commit_search_step()
        self.assistant_data["_assistant_workspace_locked"] = True
        self.assistant_data["_assistant_reopen_step"] = 2
        self.assistant_data["_search_has_run"] = True

        if getattr(self, "assistant_sim_running", False):
            return

        if not hasattr(self, "assistant_top_tree"):
            return

        if method_override:
            self.assistant_data["search_method"] = method_override
            if hasattr(self, "assistant_search_method_var"):
                self.assistant_search_method_var.set(method_override)

        if seed_result is not None:
            self.assistant_data["continue_from_signature"] = str(self._assistant_result_signature(seed_result))

        existing = self._assistant_all_results()
        if not existing:
            self._assistant_fill_top_results([])

        requested_cpu_workers = self._resolve_cpu_threads()
        cpu_workers = self._assistant_effective_worker_count(self.assistant_data.get("search_method"), requested_cpu_workers)
        manual_search = self.assistant_data.get("search_method") == "Brute Force" or self.assistant_data.get("search_time") == "Manuel"
        if hasattr(self, "assistant_search_status_var"):
            seconds = search_time_to_seconds(self.assistant_data.get("search_time", "5 min"))
            prefix = self._tr("Søger videre") if seed_result is not None else self._tr("Simulerer")
            if manual_search:
                if self._language_code() == "en":
                    self.assistant_search_status_var.set(f"{prefix} until Stop simulator… existing results are kept")
                else:
                    self.assistant_search_status_var.set(f"{prefix} indtil Stop simulator… eksisterende resultater bevares")
            else:
                if self._language_code() == "en":
                    self.assistant_search_status_var.set(f"{prefix} for {self._format_seconds(seconds)}… existing results are kept")
                else:
                    self.assistant_search_status_var.set(f"{prefix} i {self._format_seconds(seconds)}… eksisterende resultater bevares")

        if hasattr(self, "assistant_search_progress"):
            self.assistant_search_progress.configure(value=0.0)

        if hasattr(self, "assistant_next_button"):
            self.assistant_next_button.config(state="disabled")
        if hasattr(self, "assistant_stop_sim_button"):
            self.assistant_stop_sim_button.config(text=self._tr("Stop simulator"), state="normal")

        if self.assistant_window is not None:
            try:
                self.assistant_window.config(cursor="watch")
            except Exception:
                pass

        self.assistant_sim_running = True
        self.assistant_sim_queue = queue.Queue(maxsize=12)
        self.assistant_sim_stop_event = threading.Event()
        self.assistant_sim_last_progress_applied = 0.0

        snapshot = copy.deepcopy(self.assistant_data)
        default_position = internal_position(snapshot.get("position", self.position_var.get()))
        try:
            xp = float(str(snapshot.get("xp", self.xp_var.get())).replace(",", "."))
        except Exception:
            xp = 205.0
        reference_points = INTENSITY_OPTIONS.get(internal_intensity(snapshot.get("xp_reference_intensity", self.xp_reference_intensity_var.get())), self._get_xp_reference_training_points())
        seed_programs = [copy.deepcopy(seed_result.program)] if seed_result is not None else None

        def progress_callback(info):
            q = getattr(self, "assistant_sim_queue", None)
            if q is not None:
                try:
                    q.put_nowait(("progress", info))
                except queue.Full:
                    # Bevar responsivt UI: smid en gammel progress-opdatering væk og læg den nyeste ind.
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        q.put_nowait(("progress", info))
                    except queue.Full:
                        pass

        def worker():
            def send_final(kind, payload):
                q = getattr(self, "assistant_sim_queue", None)
                if q is None:
                    return
                while True:
                    try:
                        q.put_nowait((kind, payload))
                        return
                    except queue.Full:
                        try:
                            q.get_nowait()
                        except queue.Empty:
                            time.sleep(0.01)

            try:
                position = internal_position(snapshot.get("position", default_position))
                worker_count = max(1, int(cpu_workers))
                if worker_count > 1:
                    results = self._assistant_parallel_timed_search(
                        snapshot=snapshot,
                        default_position=default_position,
                        xp=xp,
                        reference_points=reference_points,
                        seed_programs=seed_programs,
                        progress_callback=progress_callback,
                        worker_count=worker_count,
                    )
                else:
                    results = assistant_search_training_plans_timed(
                        position=position,
                        start_stats=snapshot.get("start_stats", {}),
                        start_age=float(snapshot.get("start_age", 15.0)),
                        end_age=float(snapshot.get("end_age", 26.0)),
                        xp=xp,
                        weights=snapshot.get("weights", POSITION_WEIGHTS[position]),
                        change_every_days=int(snapshot.get("change_every_days", 3)),
                        search_method=snapshot.get("search_method", "Beam Search"),
                        search_time=snapshot.get("search_time", "5 min"),
                        xp_variance=bool(snapshot.get("xp_variance", False)),
                        reference_training_points=reference_points,
                        experience_bonus_enabled=bool(snapshot.get("ebr_enabled", False)),
                        experience_bonus_ratio=float(snapshot.get("ebr_ratio", 1.10)),
                        experience_bonus_knee=snapshot.get("ebr_knee", "linear"),
                        allow_intensity_boost=bool(snapshot.get("allow_intensity_boost", False)),
                        intensity_boost_uses=int(snapshot.get("intensity_boost_uses", 1)),
                        intensity_boost_period_days=int(snapshot.get("intensity_boost_period_days", 7)),
                        group_high_weighted_stats=bool(snapshot.get("group_high_weighted_stats", True)),
                        penalty_tolerance=snapshot.get("penalty_tolerance", "Høj"),
                        roll_enabled=bool(snapshot.get("roll_enabled", False)),
                        roll_block_days=int(snapshot.get("roll_block_days", 7)),
                        training_match_prelude_enabled=bool(snapshot.get("training_match_prelude_enabled", False)),
                        training_match_until_age=snapshot.get("training_match_until_age", None),
                        top_n=10,
                        progress_callback=progress_callback,
                        stop_event=self.assistant_sim_stop_event,
                        seed_programs=seed_programs,
                    )
                send_final("done", results)
            except Exception as error:
                send_final("error", error)

        self.assistant_sim_thread = threading.Thread(target=worker, daemon=True)
        self.assistant_sim_thread.start()
        self._assistant_poll_simulator_search()

    def _assistant_stop_simulator_search(self):
        stop_event = getattr(self, "assistant_sim_stop_event", None)
        if stop_event is not None:
            stop_event.set()
        if hasattr(self, "assistant_search_status_var"):
            self.assistant_search_status_var.set(self._tr("Stopper efter nuværende simulering…"))
        if hasattr(self, "assistant_stop_sim_button"):
            self.assistant_stop_sim_button.config(text=self._tr("Stopper…"), state="disabled")

    def _assistant_poll_simulator_search(self):
        q = getattr(self, "assistant_sim_queue", None)
        if q is None:
            return

        finished = False
        latest_progress = None
        processed = 0
        try:
            # Coalescér progress-beskeder. Ved Queen Seeker kan der ellers ligge mange
            # gamle UI-opdateringer, som får Tkinter til at virke frossen.
            while processed < 24:
                kind, payload = q.get_nowait()
                processed += 1
                if kind == "progress":
                    latest_progress = payload
                elif kind == "done":
                    pool = self._assistant_merge_search_results(payload)
                    self.assistant_data["_search_has_run"] = True
                    self.assistant_data["_assistant_workspace_locked"] = True
                    self.assistant_data["_assistant_reopen_step"] = 2
                    self._assistant_fill_top_results(pool)
                    self._assistant_store_recall_snapshot()
                    if getattr(self, "active_group", None) and pool:
                        self._schedule_group_report_recompute(delay=120)
                    if hasattr(self, "assistant_search_status_var"):
                        if pool:
                            if self._language_code() == "en":
                                self.assistant_search_status_var.set(f"Done. Accumulated results: {len(pool)} · best rating: {self._assistant_primary_rating_for_display(pool[0]):.2f}")
                            else:
                                self.assistant_search_status_var.set(f"Færdig. Akkumulerede resultater: {len(pool)} · bedste VT: {self._assistant_primary_rating_for_display(pool[0]):.2f}")
                        else:
                            self.assistant_search_status_var.set(self._tr("Færdig. Ingen resultater."))
                    if hasattr(self, "assistant_search_progress"):
                        self.assistant_search_progress.configure(value=100.0)
                    finished = True
                    break
                elif kind == "error":
                    raise payload
        except queue.Empty:
            pass
        except Exception as error:
            self._assistant_finish_simulator_ui()
            self._assistant_handle_error(error)
            return

        if latest_progress is not None and not finished:
            self._assistant_apply_simulator_progress(latest_progress)

        if finished:
            self._assistant_finish_simulator_ui()
            return

        if getattr(self, "assistant_sim_running", False):
            if self.assistant_window is not None and self.assistant_window.winfo_exists():
                delay = 120 if processed else 250
                self.assistant_window.after(delay, self._assistant_poll_simulator_search)
            else:
                self._assistant_stop_simulator_search()

    def _assistant_apply_simulator_progress(self, info):
        results = info.get("results") or []
        now = time.monotonic()
        last_applied = float(getattr(self, "assistant_sim_last_progress_applied", 0.0) or 0.0)
        if results and now - last_applied >= 1.0:
            pool = self._assistant_merge_search_results(results)
            self.assistant_data["_search_has_run"] = True
            self.assistant_data["_assistant_workspace_locked"] = True
            self.assistant_data["_assistant_reopen_step"] = 2
            self._assistant_fill_top_results(pool)
            self._assistant_store_recall_snapshot()
            if getattr(self, "active_group", None) and pool:
                self._schedule_group_report_recompute(delay=250)
            self.assistant_sim_last_progress_applied = now
        else:
            pool = self._assistant_all_results()

        elapsed = float(info.get("elapsed", 0.0))
        manual_progress = bool(info.get("manual", False))
        remaining = max(0.0, float(info.get("remaining", 0.0)))
        total = max(0.001, elapsed + remaining)
        percent = 0.0 if manual_progress else max(0.0, min(100.0, elapsed / total * 100.0))
        evaluated = int(info.get("evaluated", 0))
        best = self._assistant_primary_rating_for_display(pool[0]) if pool else info.get("best_rating")

        if hasattr(self, "assistant_search_progress"):
            self.assistant_search_progress.configure(value=percent)

        if hasattr(self, "assistant_search_status_var"):
            if self._language_code() == "en":
                best_text = f" · best rating {best:.2f}" if best is not None else ""
                if manual_progress:
                    self.assistant_search_status_var.set(
                        f"Brute Force… {evaluated} outcomes · total {len(pool)} · runs until Stop{best_text}"
                    )
                else:
                    self.assistant_search_status_var.set(
                        f"Simulating… {evaluated} new outcomes · total {len(pool)} · remaining {self._format_seconds(remaining)}{best_text}"
                    )
            else:
                best_text = f" · bedste VT {best:.2f}" if best is not None else ""
                if manual_progress:
                    self.assistant_search_status_var.set(
                        f"Brute Force… {evaluated} udfald · samlet {len(pool)} · kører til Stop{best_text}"
                    )
                else:
                    self.assistant_search_status_var.set(
                        f"Simulerer… {evaluated} nye udfald · samlet {len(pool)} · tilbage {self._format_seconds(remaining)}{best_text}"
                    )

    def _assistant_finish_simulator_ui(self):
        self.assistant_sim_running = False
        if hasattr(self, "assistant_next_button"):
            self.assistant_next_button.config(state="normal")
        if hasattr(self, "assistant_stop_sim_button"):
            self.assistant_stop_sim_button.config(text=self._tr("Start simulator"), state="normal")
        if self.assistant_window is not None:
            try:
                self.assistant_window.config(cursor="")
            except Exception:
                pass

    @staticmethod
    def _format_seconds(seconds):
        seconds = max(0, int(round(float(seconds))))
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:d}:{secs:02d}"

    def _set_template_name(self, name, dirty=False):
        self.current_template_name = name or "Skabelon"
        self.template_dirty = bool(dirty)
        self._refresh_template_name_display()

    def _refresh_template_name_display(self):
        name = getattr(self, "current_template_name", "Skabelon") or "Skabelon"
        if getattr(self, "template_dirty", False):
            name = f"{name}*"
        self.selected_template_name_var.set(name)

    def _refresh_presets_menu(self):
        menu = getattr(self, "presets_menu", None)
        if menu is None:
            return
        try:
            menu.delete(0, "end")
        except Exception:
            return

        for preset_name in ("Sheikh_k", "Sheikh_f", "Sheikh_m", "Sheikh_a"):
            preset_data = BUILTIN_PRESETS.get(preset_name)
            if preset_data is None:
                menu.add_command(
                    label=f"{preset_name} — kommer senere",
                    state="disabled",
                )
            else:
                menu.add_command(
                    label=preset_name,
                    command=lambda name=preset_name: self._load_builtin_preset(name),
                )

        groups = list(getattr(self, "group_import_groups", []) or [])
        if groups:
            menu.add_separator()
            menu.add_command(label=self._group_text("Grupper", "Groups"), state="disabled")
            used = {}
            for group in groups:
                try:
                    name = str(group.get("name", "Gruppe") or "Gruppe")
                    count = len(group.get("players", []) or [])
                    base_label = f"{name} ({count})"
                    used[base_label] = used.get(base_label, 0) + 1
                    label = base_label if used[base_label] == 1 else f"{base_label} #{used[base_label]}"
                    group_id = group.get("id") or name
                    menu.add_command(
                        label=label,
                        command=lambda gid=group_id: self._load_group_from_template_menu(gid),
                    )
                except Exception:
                    continue

        menu.add_separator()
        menu.add_command(label=self._tr("Gem som skabelon..."), command=self._save_program)
        menu.add_command(label=self._tr("Indlæs skabelon fra fil..."), command=self._load_program)

    def _load_group_from_template_menu(self, group_id):
        target = None
        for group in list(getattr(self, "group_import_groups", []) or []):
            try:
                if (group.get("id") or group.get("name")) == group_id:
                    target = group
                    break
            except Exception:
                continue
        if not target:
            self._main_notice(self._group_text("Gruppen findes ikke længere.", "The group no longer exists."))
            return
        name = str(target.get("name", "Gruppe") or "Gruppe")
        if self._activate_group_data_to_main(copy.deepcopy(target), undo_label=self._group_text("Aktiv gruppe", "Active group")):
            self._set_template_name(self._group_text(f"Gruppe: {name}", f"Group: {name}"), dirty=False)
            try:
                self._refresh_presets_menu()
            except Exception:
                pass

    def _mark_template_dirty(self):
        if getattr(self, "_suppress_template_dirty", False):
            return
        if not getattr(self, "template_dirty", False):
            self.template_dirty = True
            self._refresh_template_name_display()

    def _ask_save_template_changes_before_switch(self):
        # 1.96: Ingen modal advarsel ved skabelonskift.
        # Ændringer kan fortrydes med ↶.
        return True


    def _build_program_context_menu(self):
        self.program_context_menu = tk.Menu(self, tearoff=0)
        self.program_context_menu.add_command(label=self._tr("Redigér valgte"), command=self._load_selected_phase_into_editor)
        self.program_context_menu.add_command(label=self._tr("Opløs blok"), command=self._dissolve_selected_block)
        self.program_context_menu.add_separator()
        self.program_context_menu.add_command(label=self._tr("Kopiér valgte"), command=self._copy_selected_phase)
        self.program_context_menu.add_command(label=self._tr("Indsæt efter valgte"), command=self._paste_after_selected_phase)
        self.program_context_menu.add_command(label=self._tr("Duplikér valgte"), command=self._duplicate_selected_phase)
        self.program_context_menu.add_separator()
        self.program_context_menu.add_command(label=self._tr("Flyt valgte op"), command=self._move_selected_up)
        self.program_context_menu.add_command(label=self._tr("Flyt valgte ned"), command=self._move_selected_down)
        self.program_context_menu.add_separator()
        self.program_context_menu.add_command(label=self._tr("Tilføj valgte til blok"), command=self._add_selected_to_block)
        self.program_context_menu.add_separator()
        self.program_context_menu.add_command(label=self._tr("Slet valgte"), command=self._remove_selected_phase)

    def _on_ebr_toggle(self):
        state = "normal" if self.ebr_enabled_var.get() else "disabled"
        if hasattr(self, "ebr_ratio_spinbox"):
            self.ebr_ratio_spinbox.config(state=state)

        self._redraw_ebr_knee_icons()
        self._settings_changed("program")

    def _select_ebr_knee(self, value):
        if not self.ebr_enabled_var.get():
            return
        self.ebr_knee_var.set(value)
        self._redraw_ebr_knee_icons()
        self._settings_changed("program")

    def _redraw_ebr_knee_icons(self):
        if not hasattr(self, "ebr_knee_canvases"):
            return

        enabled = self.ebr_enabled_var.get() if hasattr(self, "ebr_enabled_var") else False
        selected = self.ebr_knee_var.get() if hasattr(self, "ebr_knee_var") else "linear"

        def curve_points(kind):
            pts = []
            for step in range(17):
                t = step / 16
                if kind == "early":
                    y_norm = 1.0 - (1.0 - t) ** 2
                elif kind == "late":
                    y_norm = t ** 2
                else:
                    y_norm = t

                x = 7 + t * 15
                y = 14 - y_norm * 9
                pts.extend([x, y])
            return pts

        for value, canvas in self.ebr_knee_canvases:
            canvas.delete("all")

            bg = "white" if enabled else "#eeeeee"
            outline = "#6aa0ff" if enabled and value == selected else "#b8b8b8"
            line = "#222222" if enabled else "#9a9a9a"

            canvas.configure(background=bg, highlightbackground=outline)

            # Alle ikoner er stigende fra venstre mod højre.
            if not self._draw_ebr_curve_icon(canvas, value, enabled):
                canvas.create_line(*curve_points(value), width=3, fill=line, smooth=True, splinesteps=24, capstyle=tk.ROUND)

    def _get_xp_reference_training_points(self):
        return INTENSITY_OPTIONS.get(internal_intensity(self.xp_reference_intensity_var.get()), 23)

    def _get_experience_bonus_settings(self):
        enabled = bool(self.ebr_enabled_var.get()) if hasattr(self, "ebr_enabled_var") else False

        try:
            ratio = float(self.ebr_ratio_var.get())
        except Exception:
            ratio = 1.10

        ratio = max(1.01, min(EBR_RATIO_MAX, ratio))
        knee = self.ebr_knee_var.get() if hasattr(self, "ebr_knee_var") else "linear"
        if knee not in {"early", "linear", "late"}:
            knee = "linear"

        return enabled, ratio, knee


    # ---------- General helpers ----------

    def _format_decimal(self, value):
        text = f"{value:.2f}"
        return text if self._language_code() == "en" else text.replace(".", ",")

    def _split_age(self, age):
        try:
            age = float(str(age).replace(",", "."))
        except Exception:
            age = 15.0

        total_days = int(round(age * 30))
        years = total_days // 30
        days = total_days % 30

        years = max(15, min(40, years))
        days = max(0, min(29, days))
        return years, days

    def _age_from_year_day(self, year_var, day_var, fallback=15.0):
        try:
            years = int(float(str(year_var.get()).replace(",", ".")))
        except Exception:
            years, _ = self._split_age(fallback)

        try:
            days = int(float(str(day_var.get()).replace(",", ".")))
        except Exception:
            _, days = self._split_age(fallback)

        years = max(15, min(40, years))
        days = max(0, min(29, days))
        return years + days / 30.0

    def _valid_age_part_values(self, year_var, day_var):
        """Returnerer kun alder, når begge felter er komplette og inden for interval.

        Det er vigtigt ved manuel indtastning: midlertidige værdier som "", "1"
        eller "400" må ikke straks clamped og skrives tilbage, fordi det får
        spinboxen til at hoppe.
        """
        year_text = str(year_var.get()).strip()
        day_text = str(day_var.get()).strip()
        if not year_text or not day_text:
            return None
        try:
            years = int(year_text)
            days = int(day_text)
        except Exception:
            return None
        if not (15 <= years <= 40 and 0 <= days <= 29):
            return None
        return years, days, years + days / 30.0

    def _set_age_part_vars(self, year_var, day_var, age):
        years, days = self._split_age(age)
        year_var.set(str(years))
        day_var.set(str(days))
        return years, days, years + days / 30.0

    def _format_age_parts(self, age):
        years, days = self._split_age(age)
        if self._language_code() == "en":
            return f"{years} years {days} days"
        return f"{years} år {days} dage"

    def _set_main_start_age_decimal(self, age):
        if not hasattr(self, "start_age_var"):
            return

        self._main_age_sync_lock = True
        try:
            if hasattr(self, "start_age_year_var") and hasattr(self, "start_age_day_var"):
                _years, _days, age_value = self._set_age_part_vars(self.start_age_year_var, self.start_age_day_var, age)
            else:
                age_value = float(str(age).replace(",", "."))
            self.start_age_var.set(f"{age_value:.10f}")
        except Exception:
            self.start_age_var.set("15.0000000000")
        finally:
            self._main_age_sync_lock = False

    def _on_main_start_age_parts_changed(self, *_):
        if getattr(self, "_main_age_sync_lock", False):
            return
        if not hasattr(self, "start_age_year_var") or not hasattr(self, "start_age_day_var"):
            return

        parsed = self._valid_age_part_values(self.start_age_year_var, self.start_age_day_var)
        if parsed is None:
            return

        _years, _days, age = parsed
        self.start_age_var.set(f"{age:.10f}")
        self._settings_changed()

    def _normalize_main_start_age_parts(self):
        if getattr(self, "_main_age_sync_lock", False):
            return
        if not hasattr(self, "start_age_year_var") or not hasattr(self, "start_age_day_var"):
            return
        old = 15.0
        try:
            old = float(str(self.start_age_var.get()).replace(",", "."))
        except Exception:
            pass
        age = self._age_from_year_day(self.start_age_year_var, self.start_age_day_var, fallback=old)
        self._set_main_start_age_decimal(age)
        self._settings_changed()

    def _assistant_current_age_value(self, prefix, fallback):
        year_var = getattr(self, f"assistant_{prefix}_age_year_var", None)
        day_var = getattr(self, f"assistant_{prefix}_age_day_var", None)
        if year_var is None or day_var is None:
            return fallback
        parsed = self._valid_age_part_values(year_var, day_var)
        if parsed is None:
            return None
        return parsed[2]

    def _set_assistant_age_value(self, prefix, age):
        year_var = getattr(self, f"assistant_{prefix}_age_year_var", None)
        day_var = getattr(self, f"assistant_{prefix}_age_day_var", None)
        decimal_var = getattr(self, f"assistant_{prefix}_age_var", None)
        if year_var is None or day_var is None:
            return
        _years, _days, age_value = self._set_age_part_vars(year_var, day_var, age)
        if decimal_var is not None:
            decimal_var.set(f"{age_value:.10f}")

    def _assistant_age_parts_changed(self, changed="start"):
        if getattr(self, "_assistant_age_sync_lock", False):
            return
        if not all(hasattr(self, name) for name in (
            "assistant_start_age_year_var", "assistant_start_age_day_var",
            "assistant_end_age_year_var", "assistant_end_age_day_var",
        )):
            return

        start = self._assistant_current_age_value("start", self.assistant_data.get("start_age", 15.0))
        end = self._assistant_current_age_value("end", self.assistant_data.get("end_age", 26.0))
        if start is None or end is None:
            return

        self._assistant_age_sync_lock = True
        try:
            if start > end:
                if changed == "end":
                    start = end
                    self._set_assistant_age_value("start", start)
                else:
                    end = start
                    self._set_assistant_age_value("end", end)
            if hasattr(self, "assistant_start_age_var"):
                self.assistant_start_age_var.set(f"{start:.10f}")
            if hasattr(self, "assistant_end_age_var"):
                self.assistant_end_age_var.set(f"{end:.10f}")
        finally:
            self._assistant_age_sync_lock = False

    def _normalize_assistant_age_parts(self, changed="start"):
        if getattr(self, "_assistant_age_sync_lock", False):
            return
        fallback_start = self.assistant_data.get("start_age", 15.0)
        fallback_end = self.assistant_data.get("end_age", 26.0)
        start = self._age_from_year_day(self.assistant_start_age_year_var, self.assistant_start_age_day_var, fallback=fallback_start)
        end = self._age_from_year_day(self.assistant_end_age_year_var, self.assistant_end_age_day_var, fallback=fallback_end)
        self._assistant_age_sync_lock = True
        try:
            if start > end:
                if changed == "end":
                    start = end
                else:
                    end = start
            self._set_assistant_age_value("start", start)
            self._set_assistant_age_value("end", end)
        finally:
            self._assistant_age_sync_lock = False

    def _is_cycle(self, item):
        return isinstance(item, TrainingCycle)

    def _item_days(self, item):
        return item.days if isinstance(item, TrainingCycle) else item.days

    def _next_block_name(self):
        existing = {
            item.name for item in self.program
            if isinstance(item, TrainingCycle)
        }
        n = 1
        while f"Blok {n}" in existing:
            n += 1
        return f"Blok {n}"

    def _flatten_item_to_phases(self, item):
        if isinstance(item, TrainingCycle):
            phases = []
            for _ in range(item.repetitions):
                phases.extend(copy.deepcopy(item.phases))
            return phases
        return [copy.deepcopy(item)]

    def _record_main_stat_debug_event(self, stat=None, reason=""):
        try:
            if not hasattr(self, "_main_stat_debug_events"):
                self._main_stat_debug_events = []
            values = {name: var.get() for name, var in getattr(self, "start_stat_vars", {}).items()}
            last = getattr(self, "_main_stat_last_values", {}) or {}
            changed = {name: {"before": last.get(name), "after": values.get(name)} for name in values if last.get(name) != values.get(name)}
            self._main_stat_last_values = dict(values)
            if not changed and not reason:
                return
            event = {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "stat": stat,
                "reason": reason,
                "changed": changed,
                "active_group": copy.deepcopy(getattr(self, "active_group", None) or {}),
                "program_days": sum(getattr(item, "days", 0) for item in getattr(self, "program", []) or []),
            }
            try:
                event["stack"] = traceback.format_stack(limit=8)
            except Exception:
                event["stack"] = []
            self._main_stat_debug_events.append(event)
            self._main_stat_debug_events = self._main_stat_debug_events[-120:]
        except Exception:
            pass

    def _on_main_stat_var_changed(self, stat):
        self._record_main_stat_debug_event(stat=stat, reason="stat_var_write")
        self._settings_changed()

    def _save_player_section_debug(self):
        try:
            initialfile = f"vman_player_section_debug_{time.strftime('%Y%m%d_%H%M%S')}.txt"
            path = filedialog.asksaveasfilename(
                title=self._group_text("Gem spiller-debug", "Save player debug"),
                initialfile=initialfile,
                defaultextension=".txt",
                filetypes=[("Text", "*.txt"), ("All files", "*.*")],
                parent=self,
            )
            if not path:
                return
            self._record_main_stat_debug_event(reason="manual_debug_save")
            data = {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "player_link_raw": getattr(self, "_player_link_raw_value", ""),
                "player_link_display": getattr(self, "_player_link_display_value", ""),
                "position": self.position_var.get() if hasattr(self, "position_var") else None,
                "age": self.start_age_var.get() if hasattr(self, "start_age_var") else None,
                "xp": self.xp_var.get() if hasattr(self, "xp_var") else None,
                "start_stats": {name: var.get() for name, var in getattr(self, "start_stat_vars", {}).items()},
                "active_group": getattr(self, "active_group", None),
                "program_days": sum(getattr(item, "days", 0) for item in getattr(self, "program", []) or []),
                "recent_stat_events": getattr(self, "_main_stat_debug_events", []) or [],
            }
            lines = ["VMAN Training Planner spiller-debug", "==========================", json.dumps(data, ensure_ascii=False, indent=2, default=str)]
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            self._main_notice(f"Spiller-debug gemt: {path}")
        except Exception as error:
            messagebox.showerror(self._group_text("Spiller-debug", "Player debug"), str(error), parent=self)

    def _main_parameter_snapshot(self):
        try:
            return {
                "position": self.position_var.get() if hasattr(self, "position_var") else None,
                "start_age": self.start_age_var.get() if hasattr(self, "start_age_var") else None,
                "start_age_year": self.start_age_year_var.get() if hasattr(self, "start_age_year_var") else None,
                "start_age_day": self.start_age_day_var.get() if hasattr(self, "start_age_day_var") else None,
                "xp": self.xp_var.get() if hasattr(self, "xp_var") else None,
                "xp_reference_intensity": self.xp_reference_intensity_var.get() if hasattr(self, "xp_reference_intensity_var") else None,
                "start_stats": {name: var.get() for name, var in getattr(self, "start_stat_vars", {}).items()},
                "ebr_enabled": self.ebr_enabled_var.get() if hasattr(self, "ebr_enabled_var") else None,
                "ebr_ratio": self.ebr_ratio_var.get() if hasattr(self, "ebr_ratio_var") else None,
                "ebr_knee": self.ebr_knee_var.get() if hasattr(self, "ebr_knee_var") else None,
            }
        except Exception:
            return {}

    def _store_main_parameter_snapshot(self):
        try:
            self._main_parameter_last_snapshot = copy.deepcopy(self._main_parameter_snapshot())
        except Exception:
            self._main_parameter_last_snapshot = None

    def _restore_main_parameter_snapshot(self):
        snapshot = copy.deepcopy(getattr(self, "_main_parameter_last_snapshot", None) or {})
        if not snapshot:
            return
        try:
            self._restoring_main_parameter_snapshot = True
            if snapshot.get("position") is not None and hasattr(self, "position_var"):
                self.position_var.set(snapshot.get("position"))
                try:
                    restored_position = internal_position(snapshot.get("position"))
                    if restored_position in POSITION_STATS:
                        self.current_stats = POSITION_STATS[restored_position]
                        self.active_position = restored_position
                        if set(getattr(self, "start_stat_vars", {}).keys()) != set(self.current_stats):
                            self._render_start_stats()
                        self._refresh_session_exercise_options(restored_position, reset_if_invalid=False)
                except Exception:
                    pass
            if snapshot.get("start_age") is not None and hasattr(self, "start_age_var"):
                self.start_age_var.set(snapshot.get("start_age"))
            if snapshot.get("start_age_year") is not None and hasattr(self, "start_age_year_var"):
                self.start_age_year_var.set(snapshot.get("start_age_year"))
            if snapshot.get("start_age_day") is not None and hasattr(self, "start_age_day_var"):
                self.start_age_day_var.set(snapshot.get("start_age_day"))
            if snapshot.get("xp") is not None and hasattr(self, "xp_var"):
                self.xp_var.set(snapshot.get("xp"))
            if snapshot.get("xp_reference_intensity") is not None and hasattr(self, "xp_reference_intensity_var"):
                self.xp_reference_intensity_var.set(snapshot.get("xp_reference_intensity"))
            if snapshot.get("ebr_enabled") is not None and hasattr(self, "ebr_enabled_var"):
                self.ebr_enabled_var.set(snapshot.get("ebr_enabled"))
            if snapshot.get("ebr_ratio") is not None and hasattr(self, "ebr_ratio_var"):
                self.ebr_ratio_var.set(snapshot.get("ebr_ratio"))
            if snapshot.get("ebr_knee") is not None and hasattr(self, "ebr_knee_var"):
                self.ebr_knee_var.set(snapshot.get("ebr_knee"))
            for stat, value in (snapshot.get("start_stats") or {}).items():
                if stat in getattr(self, "start_stat_vars", {}):
                    self.start_stat_vars[stat].set(value)
        except Exception:
            pass
        finally:
            self._restoring_main_parameter_snapshot = False
        try:
            self._update_start_rating_label()
            self._update_total_days()
            self._schedule_auto_simulate()
            self._schedule_workspace_autosave()
        except Exception:
            pass

    def _settings_changed(self, change_scope="player"):
        if getattr(self, "_restoring_main_parameter_snapshot", False):
            try:
                self._update_start_rating_label()
                self._update_total_days()
            except Exception:
                pass
            return

        if getattr(self, "_applying_app_settings", False):
            try:
                self._update_start_rating_label()
                self._update_total_days()
            except Exception:
                pass
            return

        scope = str(change_scope or "player").strip().lower()
        is_player_change = scope == "player"

        # Importing a result writes the Assistant's unchanged scenario back to
        # Player while installing the chosen programme. These trace events are
        # synchronization, not new player input, and must preserve the search.
        if getattr(self, "_assistant_result_import_in_progress", False):
            is_player_change = False

        can_reset_assistant = (
            is_player_change
            and not getattr(self, "_undo_suspended", False)
            and not getattr(self, "_restoring_undo", False)
        )
        if can_reset_assistant and self._assistant_has_search_state():
            if not self._assistant_confirm_search_reset_from_main():
                self._restore_main_parameter_snapshot()
                return

        self._record_undo_change("Ændring")
        self._update_start_rating_label()
        self._update_total_days()
        self._schedule_auto_simulate()
        self._mark_template_dirty()
        self._schedule_workspace_autosave()

        if can_reset_assistant:
            self._assistant_reset_from_main_window(
                "Assistenten er nulstillet efter ændringer i Spiller-sektionen."
            )

        # Only accepted Player edits advance the rollback baseline used if the
        # user declines the warning. Programme edits must not redefine it.
        if is_player_change and not self._assistant_has_search_state():
            self._store_main_parameter_snapshot()

        if getattr(self, "active_group", None):
            try:
                self._store_active_group_program_snapshot()
            except Exception:
                pass
            try:
                self._schedule_group_report_recompute(delay=300)
            except Exception:
                pass

    def _schedule_auto_simulate(self):
        if self._auto_after_id is not None:
            try:
                self.after_cancel(self._auto_after_id)
            except Exception:
                pass
        self._auto_after_id = self.after(250, self._auto_simulate)

    # ---------- Position/start stats ----------

    def _position_group(self, position):
        position = internal_position(position)
        return "keeper" if position == "Keepere" else "markspiller"

    def _sheikh_template_for_position(self, position):
        """Returnér dertil positionspassende Sheikh-skabelon."""
        position = internal_position(position)
        # Fast mapping:
        # Sheikh_k = keeper, Sheikh_f = forsvar, Sheikh_m = midtbane, Sheikh_a = angreb.
        return {
            "Keepere": "Sheikh_k",
            "Forsvar": "Sheikh_f",
            "Midtbane": "Sheikh_m",
            "Angreb": "Sheikh_a",
        }.get(position)

    def _auto_load_sheikh_template_for_position(self, position):
        """Skift automatisk Sheikh-skabelon ved manuel positionsændring."""
        if not self._setting_bool("auto_template", True):
            return False
        preset_name = self._sheikh_template_for_position(position)
        if not preset_name:
            return False
        if getattr(self, "_suppress_template_dirty", False) or getattr(self, "_restoring_undo", False):
            return False
        if getattr(self, "current_template_name", None) == preset_name and not getattr(self, "template_dirty", False):
            return False
        if preset_name not in BUILTIN_PRESETS:
            return False
        if not self._confirm_overwrite_action(f"Skift til skabelonen {preset_name}"):
            return False
        self._load_builtin_preset(preset_name, prompt_if_existing=False)
        return True

    def _on_position_change(self, initial=False):
        old_position = getattr(self, "active_position", internal_position(self.position_var.get()))
        new_position = internal_position(self.position_var.get())

        old_group = self._position_group(old_position)
        new_group = self._position_group(new_position)
        crosses_keeper_mark = old_group != new_group

        if not initial and old_position != new_position and not getattr(self, "_undo_suspended", False):
            self._push_undo("Skift position")
            if self._auto_load_sheikh_template_for_position(new_position):
                return

        previous_values = {}
        if hasattr(self, "start_stat_vars"):
            previous_values = {stat: var.get() for stat, var in self.start_stat_vars.items()}

        self.position_var.set(self._display_position(new_position))
        self.current_stats = POSITION_STATS[new_position]

        stat_names_changed = set(previous_values.keys()) != set(self.current_stats)
        if initial or stat_names_changed:
            self._render_start_stats()
            if not crosses_keeper_mark:
                for stat, value in previous_values.items():
                    if stat in self.start_stat_vars:
                        self.start_stat_vars[stat].set(value)

        if self.program and not initial and crosses_keeper_mark:
            self.program = []
            self.clipboard_phases = []
            self.editing_index = None

        if hasattr(self, "program_tree"):
            self._refresh_program_tree()

        self._refresh_session_exercise_options(new_position, reset_if_invalid=False)
        self.active_position = new_position
        self._settings_changed()

    def _get_start_stats(self):
        stats = {}
        for stat, var in self.start_stat_vars.items():
            try:
                value = int(var.get())
            except ValueError:
                raise ValueError(f"Startværdi for {stat} skal være et heltal.")

            if value < 0 or value > 100:
                raise ValueError(f"Startværdi for {stat} skal være mellem 0 og 100.")
            stats[stat] = value

        return stats

    # ---------- Distribution editor ----------

    def _on_intensity_change(self):
        # 1.73: Når intensitet ændres, skal pointfordelingen følge med live.
        # Manuelle bruger-valg bevares så vidt muligt, og resten justeres efter
        # samme låse-/autofill-filosofi som ved klik i pointfordelingen.
        if getattr(self, "dist_vars", None):
            self._smart_autofill_distribution(changed_stat=None)
        self._update_sum_label()

    def _get_phase_training_points(self):
        return INTENSITY_OPTIONS.get(internal_intensity(self.intensity_var.get()), 23)

    def _refresh_distribution_inputs(self):
        position = internal_position(self.position_var.get())
        exercise = internal_exercise(self.exercise_var.get())

        self.dist_auto_locked_stats = set()

        if exercise == "Træningskamp":
            self.dist_frame.grid_remove()
            self.dist_vars = {}
            self.dist_box_canvases = {}
            self._apply_session_technique_layout_baseline()
            self._apply_session_interval_baseline_width()
            self._schedule_right_section_sync()
            return

        self.dist_frame.grid()
        self._apply_session_technique_layout_baseline()
        self._apply_session_interval_baseline_width()

        for child in self.dist_frame.winfo_children():
            child.destroy()

        self.dist_vars = {}
        self.dist_box_canvases = {}

        stats = exercises_for_position(position)[exercise]

        ttk.Label(
            self.dist_frame,
            text="Egenskab",
            font=("", 10, "bold"),
            width=getattr(self, "_session_stat_label_width_chars", 12),
        ).grid(row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 6))

        ttk.Label(
            self.dist_frame,
            text="Indsats",
            font=("", 10, "bold"),
        ).grid(row=0, column=1, sticky="w", pady=(0, 6))

        reset_label = ttk.Label(
            self.dist_frame,
            text=("Clear" if self._language_code() == "en" else "Nulstil"),
            foreground="#3f8f1f",
            font=("", 10, "bold"),
        )
        reset_label.grid(row=0, column=2, sticky="w", padx=(10, 0), pady=(0, 6))
        reset_label.bind("<Button-1>", lambda _event: self._reset_distribution_inputs())

        for i, stat in enumerate(stats, start=1):
            ttk.Label(
                self.dist_frame,
                text=self._display_stat(stat),
                font=("", 10, "bold"),
                width=getattr(self, "_session_stat_label_width_chars", 12),
            ).grid(row=i, column=0, sticky="w", padx=(0, 12), pady=4)

            var = tk.IntVar(value=2)
            self.dist_vars[stat] = var

            canvas = tk.Canvas(
                self.dist_frame,
                width=180,
                height=18,
                highlightthickness=0,
                borderwidth=0,
                background=self.cget("bg"),
            )
            canvas.grid(row=i, column=1, sticky="w", pady=4)
            self.dist_box_canvases[stat] = canvas

            canvas.bind("<Button-1>", lambda event, stat=stat: self._set_distribution_from_pointer(stat, event.x))
            canvas.bind("<B1-Motion>", lambda event, stat=stat: self._set_distribution_from_pointer(stat, event.x))
            var.trace_add("write", lambda *_args, stat=stat: self._draw_distribution_boxes(stat))

            self._draw_distribution_boxes(stat)

        self.dist_frame.columnconfigure(1, weight=1)
        self._apply_session_technique_layout_baseline()
        self._lock_session_buttons_to_technique_baseline()
        self._apply_session_interval_baseline_width()

        # Øvelsen starter altid autoudfyldt, så summen matcher intensiteten.
        self._autofill_distribution(reset_locks=True)
        self._schedule_right_section_sync()

    def _distribution_value_from_x(self, x):
        box_w = 18
        clicked = int(x // box_w) + 1
        return max(2, min(10, clicked))

    def _set_distribution_from_pointer(self, stat, x):
        if stat not in self.dist_vars:
            return

        requested_value = self._distribution_value_from_x(x)
        value = self._clamp_manual_distribution_value(stat, requested_value)

        if self.dist_vars[stat].get() == value and stat in self.dist_auto_locked_stats:
            return

        self.dist_vars[stat].set(value)

        # Brugeren har nu redigeret denne egenskab. Den må ikke autoudfyldes,
        # før alle egenskaber i øvelsen har været manuelt redigeret.
        self.dist_auto_locked_stats.add(stat)

        if len(self.dist_auto_locked_stats) >= len(self.dist_vars):
            # Hele rækken er gennemgået. Start en ny låserunde, hvor den senest
            # redigerede egenskab stadig fastholdes, mens de øvrige kan justeres.
            self.dist_auto_locked_stats = {stat}

        self._smart_autofill_distribution(changed_stat=stat)
        self._update_sum_label()

    def _clamp_manual_distribution_value(self, changed_stat, requested_value):
        target = self._get_phase_training_points()
        stats = list(self.dist_vars.keys())

        # Simulér låsningen efter denne brugerændring.
        future_locked = set(self.dist_auto_locked_stats)
        future_locked.add(changed_stat)

        if len(future_locked) >= len(stats):
            # Når hele rækken er gennemgået, starter næste runde med den senest
            # redigerede egenskab som eneste låste værdi.
            future_locked = {changed_stat}

        other_locked_sum = 0
        adjustable_count = 0

        for stat in stats:
            if stat == changed_stat:
                continue
            if stat in future_locked:
                other_locked_sum += int(self.dist_vars[stat].get())
            else:
                adjustable_count += 1

        # De automatiske stats skal mindst kunne få 2 hver.
        max_value = target - other_locked_sum - 2 * adjustable_count
        max_value = max(2, min(10, max_value))

        return max(2, min(int(requested_value), max_value))


    def _draw_distribution_boxes(self, stat):
        canvas = self.dist_box_canvases.get(stat)
        var = self.dist_vars.get(stat)
        if canvas is None or var is None:
            return

        try:
            value = int(var.get())
        except Exception:
            value = 0

        value = max(0, min(10, value))
        canvas.delete("all")

        box_w = 18
        box_h = 18

        for i in range(10):
            x1 = i * box_w
            y1 = 0
            x2 = x1 + box_w
            y2 = y1 + box_h

            fill = "#4caf50" if i < value else "#f4f4f4"
            outline = "#777777" if i < value else "#aaaaaa"

            canvas.create_rectangle(
                x1,
                y1,
                x2,
                y2,
                fill=fill,
                outline=outline,
                width=1,
            )

    def _reset_distribution_inputs(self):
        self.dist_auto_locked_stats = set()
        self._autofill_distribution(reset_locks=True)

    def _update_sum_label(self):
        if not hasattr(self, "dist_frame"):
            return

        # Optællingen vises ikke længere i UI.
        # Valideringen findes stadig internt, så ugyldige fordelinger ikke kan gemmes.
        self.dist_frame.config(text="Pointfordeling")


    def _set_distribution_value(self, stat, value):
        if stat not in self.dist_vars:
            return

        value = max(2, min(10, int(value)))
        self.dist_vars[stat].set(value)
        self._draw_distribution_boxes(stat)

    def _distribute_remaining_points(self, adjustable_stats, remaining_points):
        if not adjustable_stats:
            return remaining_points == 0

        # Først sættes alle justerbare stats til minimum.
        for stat in adjustable_stats:
            self._set_distribution_value(stat, 2)

        remaining_extra = remaining_points - 2 * len(adjustable_stats)

        if remaining_extra < 0:
            # Der er ikke plads nok til minimum 2 på alle.
            return False

        # Fordel overskydende point jævnt og deterministisk i visningsrækkefølgen.
        while remaining_extra > 0:
            changed = False
            for stat in adjustable_stats:
                if remaining_extra <= 0:
                    break

                current = int(self.dist_vars[stat].get())
                if current < 10:
                    self._set_distribution_value(stat, current + 1)
                    remaining_extra -= 1
                    changed = True

            if not changed:
                break

        return remaining_extra == 0

    def _smart_autofill_distribution(self, changed_stat=None):
        if not self.dist_vars:
            return

        target = self._get_phase_training_points()

        locked_stats = [
            stat for stat in self.dist_vars
            if stat in self.dist_auto_locked_stats
        ]
        adjustable_stats = [
            stat for stat in self.dist_vars
            if stat not in self.dist_auto_locked_stats
        ]

        locked_sum = sum(int(self.dist_vars[stat].get()) for stat in locked_stats)
        remaining = target - locked_sum

        if adjustable_stats:
            ok = self._distribute_remaining_points(adjustable_stats, remaining)

            if not ok and changed_stat in self.dist_vars:
                # Ekstra sikkerhed: hvis en manuel værdi alligevel gør
                # fordelingen umulig, reduceres den indtil resten kan få min. 2.
                other_locked = [
                    stat for stat in locked_stats
                    if stat != changed_stat
                ]
                other_sum = sum(int(self.dist_vars[stat].get()) for stat in other_locked)
                max_changed = target - other_sum - 2 * len(adjustable_stats)
                max_changed = max(2, min(10, max_changed))
                self._set_distribution_value(changed_stat, min(int(self.dist_vars[changed_stat].get()), max_changed))

                locked_sum = sum(int(self.dist_vars[stat].get()) for stat in locked_stats)
                remaining = target - locked_sum
                self._distribute_remaining_points(adjustable_stats, remaining)

        # Sidste sikkerhed: der må aldrig være overskud.
        total = sum(int(var.get()) for var in self.dist_vars.values())
        if total > target:
            overflow = total - target
            candidates = [
                stat for stat in self.dist_vars
                if stat not in self.dist_auto_locked_stats
            ] or list(self.dist_vars.keys())

            for stat in reversed(candidates):
                if overflow <= 0:
                    break
                current = int(self.dist_vars[stat].get())
                reducible = max(0, current - 2)
                take = min(reducible, overflow)
                if take:
                    self._set_distribution_value(stat, current - take)
                    overflow -= take

        self._update_sum_label()

    def _autofill_distribution(self, reset_locks=False):
        if not self.dist_vars:
            return

        if reset_locks:
            self.dist_auto_locked_stats = set()

        target = self._get_phase_training_points()
        stats = list(self.dist_vars.keys())

        self._suppress_distribution_auto = True
        try:
            self._distribute_remaining_points(stats, target)
        finally:
            self._suppress_distribution_auto = False

        self._update_sum_label()


    def _get_distribution_from_inputs(self):
        position = internal_position(self.position_var.get())
        exercise = internal_exercise(self.exercise_var.get())

        if exercise == "Træningskamp":
            return None

        distribution = {}
        for stat, var in self.dist_vars.items():
            try:
                distribution[stat] = int(var.get())
            except ValueError:
                raise ValueError(f"Point for {stat} skal være et heltal.")

        validate_distribution(
            position,
            exercise,
            distribution,
            required_total=self._get_phase_training_points(),
        )
        return distribution

    def _build_instruction_from_editor(self):
        days = int(self.days_var.get())
        if days <= 0:
            raise ValueError("Dage skal være positivt.")

        return TrainingInstruction(
            days=days,
            exercise=internal_exercise(self.exercise_var.get()),
            distribution=self._get_distribution_from_inputs(),
            training_points=self._get_phase_training_points(),
        )


    def _program_tree_distribution_width(self):
        tree = getattr(self, "program_tree", None)
        if tree is None:
            return 475
        try:
            total_width = int(tree.winfo_width() or 0)
        except Exception:
            total_width = 0
        fixed_width = 42 + 60 + 175 + 95
        return max(260, total_width - fixed_width) if total_width > 0 else 475

    def _lock_program_tree_column_widths(self):
        """Restore the intended Træningsprogram column widths."""
        tree = getattr(self, "program_tree", None)
        if tree is None:
            return
        distribution_width = self._program_tree_distribution_width()
        try:
            tree.column("delete", width=42, minwidth=42, stretch=False)
            tree.column("days", width=60, minwidth=60, stretch=False)
            tree.column("exercise", width=175, minwidth=175, stretch=False)
            tree.column("intensity", width=95, minwidth=95, stretch=False)
            tree.column("distribution", width=distribution_width, minwidth=distribution_width, stretch=False)
        except Exception:
            pass
        try:
            self.after_idle(self._update_program_tree_column_separators)
        except Exception:
            pass

    def _program_tree_column_resize_hit(self, event):
        """Detect attempts to drag Træningsprogram column separators.

        On Windows/Tk, identify_region is not always enough, so we also check
        the x-position against the actual column boundaries.
        """
        tree = getattr(self, "program_tree", None)
        if tree is None:
            return False
        try:
            region = tree.identify_region(event.x, event.y)
            if region == "separator":
                return True

            # Only block in the heading area. Regular row drag/select must still work.
            if region not in {"heading", "separator"}:
                return False

            widths = [
                int(tree.column("delete", "width")),
                int(tree.column("days", "width")),
                int(tree.column("exercise", "width")),
                int(tree.column("intensity", "width")),
            ]
            boundary = 0
            for width in widths:
                boundary += width
                if abs(int(event.x) - int(boundary)) <= 6:
                    return True
        except Exception:
            return False
        return False

    def _resize_program_tree_columns(self, event=None, force=False):
        """Keep Træningsprogram columns inside the visible table.

        Delete/days/exercise/intensity are fixed. The distribution/session column
        takes the remaining width, and user resizing is immediately restored.
        """
        tree = getattr(self, "program_tree", None)
        if tree is None:
            return
        if getattr(self, "_program_tree_resizing", False):
            return
        try:
            total_width = int(event.width if event is not None else tree.winfo_width())
        except Exception:
            return
        if total_width <= 0:
            return
        if not force and getattr(self, "_last_program_tree_total_width", None) == total_width:
            return
        fixed_width = 42 + 60 + 175 + 95
        distribution_width = max(260, total_width - fixed_width)
        current_distribution = None
        try:
            current_distribution = int(tree.column("distribution", "width"))
        except Exception:
            pass
        if not force and current_distribution == distribution_width:
            self._last_program_tree_total_width = total_width
            return
        self._program_tree_resizing = True
        try:
            tree.column("delete", width=42, minwidth=42, stretch=False)
            tree.column("days", width=60, minwidth=60, stretch=False)
            tree.column("exercise", width=175, minwidth=175, stretch=False)
            tree.column("intensity", width=95, minwidth=95, stretch=False)
            tree.column("distribution", width=distribution_width, minwidth=distribution_width, stretch=False)
            self._last_program_tree_total_width = total_width
            self.after_idle(self._update_program_tree_column_separators)
        except Exception:
            pass
        finally:
            self._program_tree_resizing = False

    # ---------- Program editing ----------

    def _add_phase(self):
        try:
            instruction = self._build_instruction_from_editor()
            self._push_undo("Tilføj session")
            self.program.append(instruction)
            self._refresh_program_tree()
            self._select_indices([len(self.program) - 1])
        except Exception as e:
            self._main_notice(str(e))

    def _update_selected_phase(self):
        try:
            idx = self._get_selected_index()
            if idx is None:
                self._main_notice("Vælg først en session i træningsprogrammet.")
                return

            if isinstance(self.program[idx], TrainingCycle):
                self._main_notice("Blokke redigeres via Antal gentagelser eller Opløs blok.")
                return

            instruction = self._build_instruction_from_editor()
            self._push_undo("Opdater session")
            self.program[idx] = instruction
            self._refresh_program_tree()
            self._select_indices([idx])
        except Exception as e:
            self._main_notice(str(e))

    def _trash_value_for_item(self, instruction):
        return "🗑"

    def _phase_to_display(self, instruction):
        trash = self._trash_value_for_item(instruction)

        if isinstance(instruction, TrainingCycle):
            phase_names = " → ".join(self._display_exercise(phase.exercise) for phase in instruction.phases)
            dist = f"{instruction.repetitions}× ({phase_names})"
            cycle_name = str(instruction.name).replace("Blok", self._tr("Blok"))
            return (trash, instruction.days, cycle_name, self._tr("Blok"), dist)

        intensity = self._display_intensity(INTENSITY_LABELS_BY_POINTS.get(
            instruction.training_points,
            str(instruction.training_points),
        ))

        if instruction.exercise == "Træningskamp":
            dist = ""
        else:
            dist = ", ".join(f"{self._display_stat(stat)}: {value}" for stat, value in (instruction.distribution or {}).items())

        return (trash, instruction.days, self._display_exercise(instruction.exercise), intensity, dist)

    def _refresh_program_tree(self):
        if not hasattr(self, "program_tree"):
            return

        current_selection = self._get_selected_indices() if hasattr(self, "program_tree") else []
        tree = self.program_tree

        # 4.48: Opdater rækkernes indhold inkrementelt i stedet for at slette
        # og genindsætte hele listen. Det reducerer synligt flash/flicker.
        existing = list(tree.get_children())
        desired_count = len(self.program)

        # Trim overskydende rækker bagerst.
        for iid in existing[desired_count:]:
            try:
                tree.delete(iid)
            except Exception:
                pass

        existing = list(tree.get_children())

        for idx, instruction in enumerate(self.program):
            iid = str(idx)
            values = self._phase_to_display(instruction)
            if idx < len(existing):
                current_iid = existing[idx]
                if current_iid != iid:
                    try:
                        tree.item(current_iid, iid=iid)
                    except Exception:
                        pass
                if tree.exists(iid):
                    try:
                        current_values = tuple(tree.item(iid, "values"))
                    except Exception:
                        current_values = ()
                    if tuple(values) != current_values:
                        tree.item(iid, values=values)
                    try:
                        tree.move(iid, "", idx)
                    except Exception:
                        pass
                else:
                    tree.insert("", idx, iid=iid, values=values)
            else:
                tree.insert("", "end", iid=iid, values=values)

        self._schedule_settings_changed("program")

        valid_selection = [idx for idx in current_selection if idx < len(self.program)]
        if valid_selection:
            self._select_indices(valid_selection)
        else:
            self._update_quantity_editor_from_selection()

    def _get_selected_indices(self):
        if not hasattr(self, "program_tree"):
            return []
        selected = self.program_tree.selection()
        indices = []
        for iid in selected:
            try:
                idx = int(iid)
            except ValueError:
                continue
            if 0 <= idx < len(self.program):
                indices.append(idx)
        return sorted(set(indices))

    def _get_selected_index(self):
        indices = self._get_selected_indices()
        if not indices:
            return None
        return indices[0]

    def _select_indices(self, indices):
        if not hasattr(self, "program_tree"):
            return

        current = self.program_tree.selection()
        if current:
            self.program_tree.selection_remove(*current)

        valid = [str(idx) for idx in indices if 0 <= idx < len(self.program)]
        for iid in valid:
            if self.program_tree.exists(iid):
                self.program_tree.selection_add(iid)

        if valid and self.program_tree.exists(valid[0]):
            self.program_tree.focus(valid[0])
            self.program_tree.see(valid[0])

        self._update_quantity_editor_from_selection()

    def _select_range(self, start, end):
        self._select_indices(list(range(start, end + 1)))

    def _load_selected_phase_into_editor(self):
        idx = self._get_selected_index()
        if idx is None or idx >= len(self.program):
            return

        item = self.program[idx]
        if isinstance(item, TrainingCycle):
            self._main_notice("Blokke redigeres med Antal gentagelser eller højreklik → Opløs blok.")
            return

        self.editing_index = idx
        instruction = item

        self.days_var.set(str(instruction.days))
        self.exercise_var.set(instruction.exercise)
        self.intensity_var.set(self._display_intensity(INTENSITY_LABELS_BY_POINTS.get(instruction.training_points, "Hård")))
        self._refresh_distribution_inputs()

        if instruction.exercise != "Træningskamp" and instruction.distribution:
            self.dist_auto_locked_stats = set()
            for stat, value in instruction.distribution.items():
                if stat in self.dist_vars:
                    self._set_distribution_value(stat, value)
            self._update_sum_label()

    def _clear_selection_and_editor(self):
        selection = self.program_tree.selection()
        if selection:
            self.program_tree.selection_remove(*selection)
        self.editing_index = None
        self.days_var.set("3")
        self.exercise_var.set(self._display_exercise("Træningskamp"))
        self.intensity_var.set(self._display_intensity("Hård"))
        self._refresh_distribution_inputs()
        self._update_quantity_editor_from_selection()

    def _copy_selected_phase(self):
        indices = self._get_selected_indices()
        if not indices:
            return
        self.clipboard_phases = [copy.deepcopy(self.program[idx]) for idx in indices]

    def _paste_after_selected_phase(self):
        if not self.clipboard_phases:
            self._main_notice("")
            return "break"

        indices = self._get_selected_indices()
        insert_at = len(self.program) if not indices else max(indices) + 1

        self._push_undo("Indsæt session")
        for offset, instruction in enumerate(self.clipboard_phases):
            self.program.insert(insert_at + offset, copy.deepcopy(instruction))

        self._refresh_program_tree()
        self._select_range(insert_at, insert_at + len(self.clipboard_phases) - 1)

    def _duplicate_selected_phase(self):
        indices = self._get_selected_indices()
        if not indices:
            return

        copies = [copy.deepcopy(self.program[idx]) for idx in indices]
        insert_at = max(indices) + 1
        self._push_undo("Duplikér session")

        for offset, instruction in enumerate(copies):
            self.program.insert(insert_at + offset, instruction)

        self._refresh_program_tree()
        self._select_range(insert_at, insert_at + len(copies) - 1)

    def _move_selected_up(self):
        indices = self._get_selected_indices()
        if not indices or indices[0] == 0:
            return

        self._push_undo("Flyt session op")
        selected_set = set(indices)
        for idx in indices:
            if idx - 1 not in selected_set:
                self.program[idx - 1], self.program[idx] = self.program[idx], self.program[idx - 1]

        self._refresh_program_tree()
        self._select_indices([idx - 1 for idx in indices])

    def _move_selected_down(self):
        indices = self._get_selected_indices()
        if not indices or indices[-1] >= len(self.program) - 1:
            return

        self._push_undo("Flyt session ned")
        selected_set = set(indices)
        for idx in reversed(indices):
            if idx + 1 not in selected_set:
                self.program[idx + 1], self.program[idx] = self.program[idx], self.program[idx + 1]

        self._refresh_program_tree()
        self._select_indices([idx + 1 for idx in indices])

    def _remove_selected_phase(self):
        indices = self._get_selected_indices()
        if not indices:
            return
        self._push_undo("Slet session")
        for idx in reversed(indices):
            self.program.pop(idx)
        self.editing_index = None
        self._refresh_program_tree()

    def _clear_program(self):
        if self.program:
            self._push_undo("Ryd program")
        self.program.clear()
        self.clipboard_phases = []
        self.editing_index = None
        self._refresh_program_tree()
        if hasattr(self, "result_text"):
            self.result_text.delete("1.0", "end")

    # ---------- Blocks ----------

    def _add_selected_to_block(self):
        indices = self._get_selected_indices()
        if not indices:
            self._main_notice("Vælg en eller flere linjer først.")
            return

        selected_items = [self.program[idx] for idx in indices]
        phases = []
        for item in selected_items:
            phases.extend(self._flatten_item_to_phases(item))

        if not phases:
            return

        self._push_undo("Lav blok")
        insert_at = min(indices)
        for idx in reversed(indices):
            self.program.pop(idx)

        block = TrainingCycle(
            name=self._next_block_name(),
            phases=phases,
            repetitions=1,
        )

        self.program.insert(insert_at, block)
        self._refresh_program_tree()
        self._select_indices([insert_at])

    def _dissolve_selected_block(self):
        indices = self._get_selected_indices()
        if len(indices) != 1:
            return

        idx = indices[0]
        item = self.program[idx]
        if not isinstance(item, TrainingCycle):
            return

        self._push_undo("Opløs blok")
        expanded = []
        for _ in range(item.repetitions):
            expanded.extend(copy.deepcopy(item.phases))

        self.program.pop(idx)
        for offset, phase in enumerate(expanded):
            self.program.insert(idx + offset, phase)

        self._refresh_program_tree()
        self._select_range(idx, idx + len(expanded) - 1)

    # ---------- Quantity editor ----------

    def _hide_quantity_editor(self):
        if not hasattr(self, "quantity_spinbox"):
            return
        try:
            self.quantity_label.pack_forget()
            self.quantity_spinbox.pack_forget()
        except Exception:
            pass
        self.quantity_label_visible = False

    def _show_quantity_editor(self):
        if not hasattr(self, "quantity_spinbox"):
            return
        if getattr(self, "quantity_label_visible", False):
            return
        self.quantity_label.pack(side="left", padx=(0, 4))
        self.quantity_spinbox.pack(side="left")
        self.quantity_label_visible = True

    def _update_quantity_editor_from_selection(self):
        if not hasattr(self, "quantity_spinbox"):
            return

        indices = self._get_selected_indices()
        self.quantity_editor_updating = True

        try:
            self.quantity_spinbox.config(state="normal")

            if len(indices) != 1:
                self.quantity_label.config(text="")
                self.quantity_var.set(1)
                self.quantity_spinbox.config(state="disabled")
                self._hide_quantity_editor()
                return

            item = self.program[indices[0]]

            if isinstance(item, TrainingCycle):
                self.quantity_label.config(text="Antal gentagelser:")
                self.quantity_var.set(max(1, int(item.repetitions)))
            else:
                self.quantity_label.config(text="Antal dage:")
                self.quantity_var.set(max(1, int(item.days)))

            self._show_quantity_editor()
            self.quantity_spinbox.config(state="normal")
        finally:
            self.quantity_editor_updating = False

    def _schedule_quantity_editor_apply(self):
        if getattr(self, "quantity_editor_updating", False):
            return

        if self.quantity_after_id is not None:
            try:
                self.after_cancel(self.quantity_after_id)
            except Exception:
                pass

        self.quantity_after_id = self.after(250, self._apply_quantity_editor)

    def _apply_quantity_editor(self):
        self.quantity_after_id = None

        if getattr(self, "quantity_editor_updating", False):
            return

        if not hasattr(self, "quantity_spinbox"):
            return

        if str(self.quantity_spinbox.cget("state")) == "disabled":
            return

        indices = self._get_selected_indices()
        if len(indices) != 1:
            return

        raw_value = str(self.quantity_var.get()).strip()
        if raw_value == "":
            return

        try:
            value = int(raw_value)
        except Exception:
            return

        value = max(1, value)
        idx = indices[0]
        if not (0 <= idx < len(self.program)):
            return

        item = self.program[idx]

        self._push_undo("Ændr antal")
        if isinstance(item, TrainingCycle):
            if item.repetitions == value:
                return
            self.program[idx] = TrainingCycle(
                name=item.name,
                phases=copy.deepcopy(item.phases),
                repetitions=value,
            )
        else:
            if item.days == value:
                return
            self.program[idx] = TrainingInstruction(
                days=value,
                exercise=item.exercise,
                distribution=copy.deepcopy(item.distribution),
                training_points=item.training_points,
            )

        self._refresh_program_tree()
        self._select_indices([idx])

    # ---------- Drag/drop ghost ----------

    def _program_item_summary_for_drag(self, item):
        if isinstance(item, TrainingCycle):
            phase_names = " → ".join(self._display_exercise(phase.exercise) for phase in item.phases)
            cycle_name = str(item.name).replace("Blok", self._tr("Blok"))
            return f"{self._days_label(item.days)}   {cycle_name}   {self._tr('Blok')}   {item.repetitions}× ({phase_names})"

        intensity = self._display_intensity(INTENSITY_LABELS_BY_POINTS.get(item.training_points, str(item.training_points)))
        if item.exercise == "Træningskamp":
            dist = ""
        else:
            dist = ", ".join(f"{self._display_stat(stat)}: {value}" for stat, value in (item.distribution or {}).items())

        parts = [self._days_label(item.days), self._display_exercise(item.exercise), intensity]
        if dist:
            parts.append(dist)
        return "   ".join(parts)

    def _create_drag_ghost(self, item, event):
        self._destroy_drag_ghost()

        try:
            self.drag_ghost = tk.Toplevel(self)
            self.drag_ghost.overrideredirect(True)
            self.drag_ghost.attributes("-topmost", True)
            try:
                self.drag_ghost.attributes("-alpha", 0.78)
            except Exception:
                pass

            text = self._program_item_summary_for_drag(item)
            self.drag_ghost_label = ttk.Label(
                self.drag_ghost,
                text=text,
                relief="solid",
                borderwidth=1,
                padding=(8, 4),
            )
            self.drag_ghost_label.pack()
            self._move_drag_ghost(event)
        except Exception:
            self.drag_ghost = None
            self.drag_ghost_label = None

    def _move_drag_ghost(self, event):
        if not self.drag_ghost:
            return

        try:
            x = self.winfo_pointerx() + 14
            y = self.winfo_pointery() + 10
            self.drag_ghost.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _destroy_drag_ghost(self):
        if self.drag_ghost is not None:
            try:
                self.drag_ghost.destroy()
            except Exception:
                pass
        self.drag_ghost = None
        self.drag_ghost_label = None


    # ---------- Drag/drop ----------


    def _program_event_has_multi_select_modifier(self, event):
        try:
            state = int(getattr(event, "state", 0) or 0)
        except Exception:
            state = 0
        # Tk modifier masks: Shift=0x0001, Control=0x0004, Mod1/2/4 vary by
        # platform. On macOS, Command is usually reported as Mod2/Mod4, while
        # the explicit <Command-Button-1> binding below catches it as well.
        return bool(state & (0x0001 | 0x0004 | 0x0008 | 0x0010 | 0x0040 | 0x0080))

    def _on_program_native_multi_select_click(self, event):
        # Gendan Cmd/Ctrl-klik til enkeltvis multi-markering og Shift-klik til
        # sammenhængende markering. Det holdes adskilt fra normal klik+træk,
        # som bruges til range-select og drag/drop-flytning.
        self.drag_source_index = None
        self.drag_source_selection = []
        self.drag_mode = None
        self.drag_select_anchor_index = None
        self.drag_select_started = False
        self.drag_is_dragging = False
        self.drag_drop_index = None
        self._destroy_drag_ghost()
        self._hide_program_drop_indicator()

        row_id = self.program_tree.identify_row(event.y) if hasattr(self, "program_tree") else ""
        if not row_id:
            return None
        try:
            idx = int(row_id)
        except Exception:
            return None
        if idx < 0 or idx >= len(self.program):
            return None

        try:
            state = int(getattr(event, "state", 0) or 0)
        except Exception:
            state = 0

        if state & 0x0001:  # Shift
            try:
                focus = self.program_tree.focus()
                anchor = int(focus) if focus else None
            except Exception:
                anchor = None
            if anchor is None or anchor < 0 or anchor >= len(self.program):
                selected = self._get_selected_indices()
                anchor = selected[0] if selected else idx
            self._select_range(min(anchor, idx), max(anchor, idx))
        else:
            try:
                if row_id in self.program_tree.selection():
                    self.program_tree.selection_remove(row_id)
                else:
                    self.program_tree.selection_add(row_id)
                self.program_tree.focus(row_id)
            except Exception:
                pass
            self._update_quantity_editor_from_selection()

        return "break"

    def _drag_selected_indices_for_event(self, row_id):
        try:
            clicked_index = int(row_id)
        except Exception:
            return []
        current = self._get_selected_indices()
        if clicked_index in current:
            return current
        return [clicked_index]

    def _index_from_program_event(self, event):
        if not hasattr(self, "program_tree"):
            return None
        row_id = self.program_tree.identify_row(event.y)
        if row_id:
            try:
                idx = int(row_id)
                if 0 <= idx < len(self.program):
                    return idx
            except Exception:
                pass

        children = self.program_tree.get_children()
        if not children:
            return None
        try:
            first_bbox = self.program_tree.bbox(children[0])
            if first_bbox and event.y < first_bbox[1]:
                return 0
            last_bbox = self.program_tree.bbox(children[-1])
            if last_bbox and event.y > last_bbox[1] + last_bbox[3]:
                return len(self.program) - 1
        except Exception:
            pass
        return None

    def _drop_index_from_event(self, event):
        if not hasattr(self, "program_tree"):
            return 0
        row_id = self.program_tree.identify_row(event.y)
        if row_id:
            try:
                idx = int(row_id)
                bbox = self.program_tree.bbox(row_id)
                if bbox:
                    _x, y, _w, h = bbox
                    return idx if event.y < y + h / 2 else idx + 1
                return idx
            except Exception:
                pass

        children = self.program_tree.get_children()
        if not children:
            return 0
        try:
            first_bbox = self.program_tree.bbox(children[0])
            if first_bbox and event.y < first_bbox[1]:
                return 0
            last_bbox = self.program_tree.bbox(children[-1])
            if last_bbox and event.y > last_bbox[1] + last_bbox[3]:
                return len(self.program)
        except Exception:
            pass
        return len(self.program)

    def _show_program_drop_indicator(self, insert_at):
        if not getattr(self, "program_drop_indicator", None) or not hasattr(self, "program_tree"):
            return
        try:
            children = self.program_tree.get_children()
            if not children:
                y = 2
            elif insert_at <= 0:
                bbox = self.program_tree.bbox(children[0])
                y = bbox[1] if bbox else 2
            elif insert_at >= len(children):
                bbox = self.program_tree.bbox(children[-1])
                y = (bbox[1] + bbox[3]) if bbox else max(2, self.program_tree.winfo_height() - 2)
            else:
                bbox = self.program_tree.bbox(children[insert_at])
                y = bbox[1] if bbox else 2
            y = max(1, min(int(y), max(1, self.program_tree.winfo_height() - 2)))
            self.program_drop_indicator.place(x=0, y=y, relwidth=1.0, height=2)
            self.program_drop_indicator.lift()
        except Exception:
            pass

    def _hide_program_drop_indicator(self):
        indicator = getattr(self, "program_drop_indicator", None)
        if indicator is not None:
            try:
                indicator.place_forget()
            except Exception:
                pass

    def _auto_scroll_program_tree_during_drag(self, event):
        try:
            height = self.program_tree.winfo_height()
            if event.y < 18:
                self.program_tree.yview_scroll(-1, "units")
            elif event.y > height - 18:
                self.program_tree.yview_scroll(1, "units")
        except Exception:
            pass

    def _move_program_items_to_index(self, indices, insert_at):
        indices = sorted({idx for idx in indices if 0 <= idx < len(self.program)})
        if not indices:
            return False
        insert_at = max(0, min(int(insert_at), len(self.program)))
        selected_set = set(indices)
        selected_items = [self.program[idx] for idx in indices]
        kept_items = [item for idx, item in enumerate(self.program) if idx not in selected_set]
        adjusted_insert = insert_at - sum(1 for idx in indices if idx < insert_at)
        adjusted_insert = max(0, min(adjusted_insert, len(kept_items)))
        new_program = kept_items[:adjusted_insert] + selected_items + kept_items[adjusted_insert:]
        if new_program == self.program:
            return False

        self._push_undo("Flyt session")
        self.program = new_program
        self._refresh_program_tree()
        self._select_indices(range(adjusted_insert, adjusted_insert + len(selected_items)))
        return True

    def _select_program_drag_range(self, event):
        if self.drag_select_anchor_index is None:
            return
        idx = self._index_from_program_event(event)
        if idx is None:
            return
        start = min(self.drag_select_anchor_index, idx)
        end = max(self.drag_select_anchor_index, idx)
        try:
            current = self.program_tree.selection()
            if current:
                self.program_tree.selection_remove(*current)
            for selection_idx in range(start, end + 1):
                iid = str(selection_idx)
                if self.program_tree.exists(iid):
                    self.program_tree.selection_add(iid)
            focus_iid = str(idx)
            if self.program_tree.exists(focus_iid):
                self.program_tree.focus(focus_iid)
        except Exception:
            self._select_range(start, end)
        self._update_quantity_editor_from_selection()
        self.drag_select_started = True

    def _debug_program_trash_click(self, stage, event=None, extra=""):
        """Write a small debug line for trash-column clicks when explicitly enabled."""
        try:
            if os.environ.get("VMAN_ENGINE_TRASH_DEBUG") != "1":
                return
            log_path = self._settings_dir() / "program_trash_click_debug.log"
            tree = getattr(self, "program_tree", None)
            row = col = region = ""
            x = y = ""
            delete_width = ""
            if event is not None and tree is not None:
                x = getattr(event, "x", "")
                y = getattr(event, "y", "")
                try:
                    row = tree.identify_row(event.y)
                except Exception:
                    row = ""
                try:
                    col = tree.identify_column(event.x)
                except Exception:
                    col = ""
                try:
                    region = tree.identify_region(event.x, event.y)
                except Exception:
                    region = ""
                try:
                    delete_width = tree.column("delete", "width")
                except Exception:
                    delete_width = ""
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(
                    f"{stage}: x={x} y={y} row={row!r} col={col!r} "
                    f"region={region!r} delete_width={delete_width!r} "
                    f"program_len={len(getattr(self, 'program', []))} {extra}\n"
                )
        except Exception:
            pass

    def _is_program_trash_click(self, event):
        """Return row_id if event is inside the delete/trash column, else empty string."""
        try:
            tree = self.program_tree
            row_id = tree.identify_row(event.y)
            if not row_id:
                return ""
            region = tree.identify_region(event.x, event.y)
            column_id = tree.identify_column(event.x)
            try:
                delete_width = int(tree.column("delete", "width"))
            except Exception:
                delete_width = 42

            # Windows/Tk can report region/column slightly differently depending on
            # theme, emoji width and padding. Treat the first delete_width pixels as
            # the trash column, as long as the click is on a real row.
            if column_id == "#1":
                return row_id
            if region in {"cell", "tree"} and int(event.x) <= delete_width + 3:
                return row_id
        except Exception:
            return ""
        return ""

    def _delete_program_row_by_iid(self, row_id):
        try:
            idx = int(row_id)
        except (TypeError, ValueError):
            return False
        if not (0 <= idx < len(self.program)):
            return False
        self._push_undo("Slet session")
        self.program.pop(idx)
        self.editing_index = None
        self._refresh_program_tree()
        return True

    def _on_program_button_press(self, event):
        row_id = self.program_tree.identify_row(event.y)
        column_id = self.program_tree.identify_column(event.x)
        region = self.program_tree.identify_region(event.x, event.y)

        if self._program_tree_column_resize_hit(event):
            self._program_column_resize_blocked = True
            self._lock_program_tree_column_widths()
            return "break"

        self._debug_program_trash_click("press", event)

        self.drag_source_index = None
        self.drag_source_selection = []
        self.drag_mode = None
        self.drag_select_anchor_index = None
        self.drag_select_started = False
        self.drag_is_dragging = False
        self.drag_drop_index = None
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        self._destroy_drag_ghost()
        self._hide_program_drop_indicator()
        self._pending_program_trash_row = None
        self._pending_program_trash_column = None

        if row_id and region in {"cell", "tree"} and self._program_event_has_multi_select_modifier(event):
            return None

        # 4.47: robust skraldespandsklik. Tk/Windows kan rapportere første
        # kolonne lidt forskelligt, så vi bruger både identify_column og x-position.
        trash_row_id = self._is_program_trash_click(event)
        if trash_row_id:
            self._pending_program_trash_row = trash_row_id
            self._pending_program_trash_column = "#1"
            self._debug_program_trash_click("press-trash-pending", event, extra=f"pending={trash_row_id!r}")
            return "break"

        if row_id and region in {"cell", "tree"}:
            try:
                idx = int(row_id)
            except ValueError:
                return None
            if 0 <= idx < len(self.program):
                current_selection = set(self.program_tree.selection())
                clicked_was_selected = row_id in current_selection
                self.drag_source_index = idx
                self.drag_start_x = event.x
                self.drag_start_y = event.y

                # UX-regel:
                # - Træk fra en allerede markeret linje flytter de markerede sessions.
                # - Træk fra en ikke-markeret linje laver en sammenhængende markering.
                # Det gør multi-markering med musen muligt uden at ødelægge flytning af flere valgte.
                if clicked_was_selected:
                    self.drag_mode = "move"
                    self.drag_source_selection = self._drag_selected_indices_for_event(row_id)
                    return "break"

                self.drag_mode = "select"
                self.drag_select_anchor_index = idx
                self._select_indices([idx])
                try:
                    self.program_tree.focus(row_id)
                except Exception:
                    pass
                return "break"

        return None

    def _on_program_drag_motion(self, event):
        if getattr(self, "_program_column_resize_blocked", False) or self._program_tree_column_resize_hit(event):
            self._program_column_resize_blocked = True
            self._lock_program_tree_column_widths()
            return "break"

        if self.drag_source_index is None:
            return None

        distance = abs(event.x - self.drag_start_x) + abs(event.y - self.drag_start_y)
        if not self.drag_is_dragging and distance < 6:
            return "break" if self.drag_mode in {"move", "select"} else None

        if self.drag_mode == "select":
            self.drag_is_dragging = True
            self._auto_scroll_program_tree_during_drag(event)
            self._select_program_drag_range(event)
            return "break"

        if self.drag_mode != "move":
            return None

        if not self.drag_is_dragging:
            selected = self._drag_selected_indices_for_event(str(self.drag_source_index))
            if selected:
                self.drag_source_selection = selected
                self._select_indices(selected)
            self.drag_is_dragging = True
            if len(self.drag_source_selection) == 1:
                item = self.program[self.drag_source_selection[0]]
                self._create_drag_ghost(item, event)
            else:
                self._destroy_drag_ghost()
                try:
                    self.drag_ghost = tk.Toplevel(self)
                    self.drag_ghost.overrideredirect(True)
                    self.drag_ghost.attributes("-topmost", True)
                    try:
                        self.drag_ghost.attributes("-alpha", 0.78)
                    except Exception:
                        pass
                    self.drag_ghost_label = ttk.Label(
                        self.drag_ghost,
                        text=f"{len(self.drag_source_selection)} {self._tr('Sessions')}",
                        relief="solid",
                        borderwidth=1,
                        padding=(8, 4),
                    )
                    self.drag_ghost_label.pack()
                    self._move_drag_ghost(event)
                except Exception:
                    self.drag_ghost = None
                    self.drag_ghost_label = None

        self._auto_scroll_program_tree_during_drag(event)
        self._move_drag_ghost(event)
        self.drag_drop_index = self._drop_index_from_event(event)
        self._show_program_drop_indicator(self.drag_drop_index)
        return "break"

    def _on_program_button_release(self, event):
        moved = False
        trash_deleted = False
        try:
            if getattr(self, "_program_column_resize_blocked", False) or self._program_tree_column_resize_hit(event):
                self._program_column_resize_blocked = False
                self._lock_program_tree_column_widths()
                return "break"

            self._debug_program_trash_click("release", event)
            row_id = self.program_tree.identify_row(event.y)
            column_id = self.program_tree.identify_column(event.x)
            region = self.program_tree.identify_region(event.x, event.y)

            trash_row_id = self._is_program_trash_click(event)
            click_distance = abs(int(getattr(event, "x", 0)) - int(getattr(self, "drag_start_x", 0))) + abs(int(getattr(event, "y", 0)) - int(getattr(self, "drag_start_y", 0)))
            if trash_row_id and not self.drag_is_dragging and click_distance < 8:
                self._debug_program_trash_click("release-trash-delete", event, extra=f"delete={trash_row_id!r} distance={click_distance}")
                trash_deleted = self._delete_program_row_by_iid(trash_row_id)
                return "break"

            if (
                self._pending_program_trash_row
                and not self.drag_is_dragging
                and row_id == self._pending_program_trash_row
                and region in {"cell", "tree"}
            ):
                self._debug_program_trash_click("release-trash-pending-delete", event, extra=f"delete={row_id!r}")
                trash_deleted = self._delete_program_row_by_iid(row_id)
                return "break"

            if self.drag_mode == "select":
                if self.drag_is_dragging:
                    self._select_program_drag_range(event)
                return "break"
            if self.drag_is_dragging and self.drag_source_selection:
                drop_index = self._drop_index_from_event(event)
                moved = self._move_program_items_to_index(self.drag_source_selection, drop_index)
        finally:
            self._pending_program_trash_row = None
            self._pending_program_trash_column = None
            self._program_column_resize_blocked = False
            self.drag_source_index = None
            self.drag_source_selection = []
            self.drag_mode = None
            self.drag_select_anchor_index = None
            self.drag_select_started = False
            self.drag_is_dragging = False
            self.drag_drop_index = None
            self._destroy_drag_ghost()
            self._hide_program_drop_indicator()
        return "break" if (moved or trash_deleted) else None

    def _on_program_trash_release_debug_fallback(self, event):
        """Fallback if normal press/release path is swallowed by Tk/Treeview."""
        try:
            trash_row_id = self._is_program_trash_click(event)
            if not trash_row_id:
                return None
            # If the normal release handler already deleted, this row id no longer exists.
            if not self.program_tree.exists(trash_row_id):
                return "break"
            self._debug_program_trash_click("fallback-release-delete", event, extra=f"delete={trash_row_id!r}")
            if self._delete_program_row_by_iid(trash_row_id):
                return "break"
        except Exception as error:
            try:
                self._debug_program_trash_click("fallback-error", event, extra=str(error))
            except Exception:
                pass
        return None

    def _show_export_menu(self):
        try:
            x = self.export_button.winfo_rootx()
            y = self.export_button.winfo_rooty() + self.export_button.winfo_height()
            self.export_menu.tk_popup(x, y)
        finally:
            self.export_menu.grab_release()


    # ---------- Context menu ----------

    def _show_program_context_menu(self, event):
        row_id = self.program_tree.identify_row(event.y)

        if row_id:
            if row_id not in self.program_tree.selection():
                self.program_tree.selection_set(row_id)
                self.program_tree.focus(row_id)

        indices = self._get_selected_indices()
        has_selection = bool(indices)
        has_clipboard = bool(self.clipboard_phases)

        selected_are_single_block = (
            len(indices) == 1
            and isinstance(self.program[indices[0]], TrainingCycle)
        )
        selected_all_regular = (
            bool(indices)
            and all(not isinstance(self.program[idx], TrainingCycle) for idx in indices)
        )

        end_index = self.program_context_menu.index("end")
        if end_index is not None:
            for index in range(end_index + 1):
                try:
                    label = self.program_context_menu.entrycget(index, "label")
                except tk.TclError:
                    continue

                canonical = UI_TEXT_EN_TO_DA.get(label, label)

                if canonical == "Redigér valgte":
                    self.program_context_menu.entryconfig(index, state=("normal" if selected_all_regular else "disabled"))
                elif canonical == "Opløs blok":
                    self.program_context_menu.entryconfig(index, state=("normal" if selected_are_single_block else "disabled"))
                elif canonical in {
                    "Kopiér valgte",
                    "Duplikér valgte",
                    "Flyt valgte op",
                    "Flyt valgte ned",
                    "Slet valgte",
                    "Tilføj valgte til blok",
                }:
                    self.program_context_menu.entryconfig(index, state=("normal" if has_selection else "disabled"))
                elif canonical == "Indsæt efter valgte":
                    self.program_context_menu.entryconfig(index, state=("normal" if has_clipboard else "disabled"))

        self.program_context_menu.tk_popup(event.x_root, event.y_root)
        self.program_context_menu.grab_release()

    def _on_program_left_click(self, event):
        region = self.program_tree.identify("region", event.x, event.y)
        row_id = self.program_tree.identify_row(event.y)
        column = self.program_tree.identify_column(event.x)

        # Skraldespandskolonnen er første kolonne.
        if region == "cell" and row_id and column == "#1":
            if self._delete_program_row_by_iid(row_id):
                return "break"
        return None


    # ---------- Double click ----------

    def _on_program_double_click(self, event):
        row_id = self.program_tree.identify_row(event.y)
        if row_id:
            self.program_tree.selection_set(row_id)
            self.program_tree.focus(row_id)
        self._load_selected_phase_into_editor()

    # ---------- Status/simulation ----------

    def _update_total_days(self):
        if not hasattr(self, "total_days_label"):
            return

        total = sum(item.days for item in self.program)
        try:
            start = float(self.start_age_var.get())
            end_age = calculate_end_age(start, total)
            if hasattr(self, "end_age_label"):
                self.end_age_label.config(text=f"Beregnet slutalder: {self._format_age_parts(end_age)}")
        except Exception:
            if hasattr(self, "end_age_label"):
                self.end_age_label.config(text="Beregnet slutalder: -")

    def _set_status_line(self, total_days, end_age=None, rating=None, average=None):
        rating_text = "-" if rating is None else self._format_decimal(rating)
        average_text = "-" if average is None else self._format_decimal(average)
        end_age_text = "-" if end_age is None else self._format_age_parts(end_age)
        if self._language_code() == "en":
            text = (
                f"Total: {total_days} days — "
                f"Rating: {rating_text} — "
                f"Average: {average_text} — "
                f"Age: {end_age_text}"
            )
        else:
            text = (
                f"Samlet: {total_days} dage — "
                f"Vurderingstal: {rating_text} — "
                f"Gennemsnit: {average_text} — "
                f"Alder: {end_age_text}"
            )
        self.total_days_label.config(text=text)

    def _auto_simulate(self):
        self._auto_after_id = None
        total_days = sum(item.days for item in self.program)

        try:
            start_age = float(self.start_age_var.get())
            end_age = calculate_end_age(start_age, total_days)
        except Exception:
            end_age = None

        if not self.program:
            self.last_history = []
            self.last_summary = None
            self._set_status_line(total_days=total_days, end_age=end_age)
            self._update_live_stats_panel()
            return

        try:
            start_stats = self._get_start_stats()
            xp = float(self.xp_var.get())
            start_age = float(self.start_age_var.get())
            position = internal_position(self.position_var.get())

            final_state, history = simulate_program(
                start_stats=start_stats,
                program=self.program,
                position=position,
                xp=xp,
                start_age=start_age,
                keep_history=True,
                reference_training_points=self._get_xp_reference_training_points(),
                experience_bonus_enabled=self._get_experience_bonus_settings()[0],
                experience_bonus_ratio=self._get_experience_bonus_settings()[1],
                experience_bonus_knee=self._get_experience_bonus_settings()[2],
                experience_bonus_start_age=start_age,
            )

            summary = summarize_state(final_state, position=position)
            normalized_position = internal_position(position)
            rating = summary[f"Vurderingstal_{normalized_position}"]

            self.last_history = history
            self.last_summary = summary
            self._set_status_line(
                total_days=total_days,
                end_age=end_age,
                rating=rating,
                average=summary["Gennemsnit"],
            )
            self._update_live_stats_panel(summary=summary, position=normalized_position)
            if getattr(self, "active_group", None):
                self._schedule_group_report_recompute(delay=450)

        except Exception:
            self._set_status_line(total_days=total_days, end_age=end_age)
            self._update_live_stats_panel()

    def _simulate(self):
        try:
            if not self.program:
                raise ValueError("Træningsprogrammet er tomt. Brug 'Tilføj til træningsprogram' først.")

            start_stats = self._get_start_stats()
            xp = float(self.xp_var.get())
            start_age = float(self.start_age_var.get())
            total_days = sum(item.days for item in self.program)
            end_age = calculate_end_age(start_age, total_days)
            position = internal_position(self.position_var.get())

            final_state, history = simulate_program(
                start_stats=start_stats,
                program=self.program,
                position=position,
                xp=xp,
                start_age=start_age,
                keep_history=True,
                reference_training_points=self._get_xp_reference_training_points(),
                experience_bonus_enabled=self._get_experience_bonus_settings()[0],
                experience_bonus_ratio=self._get_experience_bonus_settings()[1],
                experience_bonus_knee=self._get_experience_bonus_settings()[2],
                experience_bonus_start_age=start_age,
            )

            summary = summarize_state(final_state, position=position)
            self.last_history = history
            self.last_summary = summary
            if getattr(self, "active_group", None):
                try:
                    self._schedule_group_report_recompute(delay=80)
                except Exception:
                    pass

            intensity_counts = {}
            for _, _, points in expand_program(self.program):
                label = self._display_intensity(INTENSITY_LABELS_BY_POINTS.get(points, str(points)))
                intensity_counts[label] = intensity_counts.get(label, 0) + 1

            lines = []
            lines.append(f"Position: {self._display_position(position)}")
            lines.append(f"Periode: {start_age:.2f} → {end_age:.2f}")
            lines.append(f"XP: {xp:.2f}")
            lines.append(f"XP ved: {self._display_intensity(internal_intensity(self.xp_reference_intensity_var.get()))}")
            ebr_enabled, ebr_ratio, ebr_knee = self._get_experience_bonus_settings()
            if ebr_enabled:
                knee_label = {"early": "tidlig", "linear": "lineær", "late": "sen"}.get(ebr_knee, "lineær")
                lines.append(f"Erfaringsbonus: aktiv — ratio {ebr_ratio:.2f} — gradient {knee_label}")
            else:
                lines.append("Erfaringsbonus: slået fra")
            lines.append(f"Programdage: {total_days}")
            lines.append(f"Simulerede dage: {len(history)}")
            lines.append("")
            lines.append("Intensitet fordelt på dage:")
            for label, days in intensity_counts.items():
                lines.append(f"  {label}: {days} dage")
            lines.append("")
            lines.append("Slutstats:")
            for stat in POSITION_STATS[position]:
                lines.append(f"  {stat}: {summary[stat]:.2f}")
            lines.append("")
            lines.append(f"Gennemsnit: {summary['Gennemsnit']:.2f}")
            lines.append(f"Vurderingstal ({self._display_position(position)}): {summary[f'Vurderingstal_{position}']:.2f}")

            if hasattr(self, "result_text"):
                self.result_text.delete("1.0", "end")
                self.result_text.insert("1.0", "\n".join(lines))
            self._settings_changed("program")

        except Exception as e:
            self._main_notice(str(e))

    def _load_builtin_preset(self, preset_name, prompt_if_existing=True):
        try:
            data = BUILTIN_PRESETS.get(preset_name)
            if data is None:
                self._main_notice(f"{preset_name} er ikke lagt ind endnu.")
                return

            if prompt_if_existing and not self._ask_save_template_changes_before_switch():
                return

            if prompt_if_existing and not self._confirm_overwrite_action(f"Skift til skabelonen {preset_name}"):
                return

            if prompt_if_existing:
                self._push_undo(f"Skabelon {preset_name}")

            self._suppress_template_dirty = True
            try:
                position = internal_position(data.get("position", "Forsvar"))

                old_position = getattr(self, "active_position", internal_position(self.position_var.get()))
                old_group = self._position_group(old_position)
                new_group = self._position_group(position)

                self.position_var.set(self._display_position(position))
                self.current_stats = POSITION_STATS[position]

                if old_group != new_group:
                    self._render_start_stats()

                self._refresh_session_exercise_options(position, reset_if_invalid=False)

                self.program = [
                    self._deserialize_program_item(item)
                    for item in data.get("program", [])
                ]
                self.clipboard_phases = []
                self.editing_index = None
                self.active_position = position

                self._refresh_program_tree()
                self._settings_changed("program")
            finally:
                self._suppress_template_dirty = False

            self._set_template_name(preset_name, dirty=False)

        except Exception as e:
            self._main_notice(str(e))


    # ---------- Save/load ----------

    def _serialize_program_item(self, item):
        if isinstance(item, TrainingCycle):
            return {
                "type": "block",
                "name": item.name,
                "repetitions": item.repetitions,
                "phases": [self._serialize_program_item(phase) for phase in item.phases],
            }

        return {
            "type": "phase",
            "days": item.days,
            "exercise": item.exercise,
            "intensity": INTENSITY_LABELS_BY_POINTS.get(item.training_points, "Hård"),
            "training_points": item.training_points,
            "distribution": item.distribution,
        }

    def _deserialize_program_item(self, item):
        item_type = item.get("type", "phase")

        if item_type in ("cycle", "block"):
            phases = [self._deserialize_program_item(phase) for phase in item.get("phases", [])]
            flat_phases = []
            for phase in phases:
                if isinstance(phase, TrainingCycle):
                    flat_phases.extend(copy.deepcopy(phase.phases) * phase.repetitions)
                else:
                    flat_phases.append(phase)

            return TrainingCycle(
                name=item.get("name", self._next_block_name()).replace("Cyklus", "Blok"),
                phases=flat_phases,
                repetitions=int(item.get("repetitions", 1)),
            )

        if "training_points" in item:
            training_points = int(item["training_points"])
        elif "intensity" in item:
            training_points = INTENSITY_OPTIONS.get(item["intensity"], 23)
        else:
            training_points = 23

        return TrainingInstruction(
            days=int(item["days"]),
            exercise=item["exercise"],
            distribution=item.get("distribution"),
            training_points=training_points,
        )

    def _save_program(self):
        try:
            path = filedialog.asksaveasfilename(
                defaultextension=".json",
                filetypes=[("JSON", "*.json")],
                title="Gem træningsprogram",
            )
            if not path:
                return False

            if Path(path).exists() and not self._confirm_overwrite_action("Overskrivning af gemt træningsprogram"):
                return False

            data = {
                "position": internal_position(self.position_var.get()),
                "start_age": self.start_age_var.get(),
                "xp_at_hard_intensity": self.xp_var.get(),
                "xp_reference_intensity": internal_intensity(self.xp_reference_intensity_var.get()),
                "experience_bonus": {
                    "enabled": bool(self.ebr_enabled_var.get()),
                    "ratio": self.ebr_ratio_var.get(),
                    "knee": self.ebr_knee_var.get(),
                },
                "start_stats": {stat: var.get() for stat, var in self.start_stat_vars.items()},
                "program": [
                    self._serialize_program_item(item)
                    for item in self.program
                ],
            }

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            self._set_template_name(Path(path).stem, dirty=False)
            return True

        except Exception as e:
            self._main_notice(str(e))
            return False



    def _load_program(self):
        try:
            if not self._ask_save_template_changes_before_switch():
                return
            if not self._confirm_overwrite_action("Indlæsning af skabelon"):
                return

            path = filedialog.askopenfilename(
                filetypes=[("JSON", "*.json")],
                title="Indlæs træningsprogram",
            )
            if not path:
                return

            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._push_undo("Indlæs skabelon")
            self._suppress_template_dirty = True
            try:
                position = internal_position(data.get("position", "Forsvar"))
                self.position_var.set(self._display_position(position))
                self.current_stats = POSITION_STATS[position]
                self._render_start_stats()

                self._set_main_start_age_decimal(data.get("start_age", "15.0"))
                self.xp_var.set(str(data.get("xp_at_hard_intensity", data.get("xp", data.get("xp_per_day", "180")))))
                self.xp_reference_intensity_var.set(str(data.get("xp_reference_intensity", "Hård")))

                ebr = data.get("experience_bonus", {})
                self.ebr_enabled_var.set(bool(ebr.get("enabled", False)))
                self.ebr_ratio_var.set(str(ebr.get("ratio", "1.10")))
                self.ebr_knee_var.set(str(ebr.get("knee", "linear")))
                self._on_ebr_toggle()

                start_stats = data.get("start_stats", {})
                for stat in self.current_stats:
                    self.start_stat_vars[stat].set(str(start_stats.get(stat, "2")))

                self.exercise_var.set(self._display_exercise("Træningskamp"))
                self._refresh_session_exercise_options(position, reset_if_invalid=True)

                self.program = []
                for item in data.get("program", []):
                    self.program.append(self._deserialize_program_item(item))

                self.editing_index = None
                self.active_position = position
                self._refresh_program_tree()
            finally:
                self._suppress_template_dirty = False

            self._set_template_name(Path(path).stem, dirty=False)

        except Exception as e:
            self._main_notice(str(e))

    # ---------- Export/graph ----------

    def _simulate_history_without_penalty(self):
        if not self.program:
            return []

        start_stats = self._get_start_stats()
        xp = float(self.xp_var.get())
        start_age = float(self.start_age_var.get())
        position = internal_position(self.position_var.get())

        _, history_without_penalty = simulate_program(
            start_stats=start_stats,
            program=self.program,
            position=position,
            xp=xp,
            start_age=start_age,
            keep_history=True,
            stat_mode_for_penalty="none",
            reference_training_points=self._get_xp_reference_training_points(),
            experience_bonus_enabled=self._get_experience_bonus_settings()[0],
            experience_bonus_ratio=self._get_experience_bonus_settings()[1],
            experience_bonus_knee=self._get_experience_bonus_settings()[2],
            experience_bonus_start_age=start_age,
        )

        return history_without_penalty


    def _show_rating_graph(self):
        try:
            lang = self._language_code()
            if not self.last_history:
                raise ValueError(
                    "No history yet. Run a simulation first."
                    if lang == "en"
                    else "Der er ingen historik endnu. Kør en simulering først."
                )

            try:
                import matplotlib.pyplot as plt
            except Exception:
                self._main_notice(
                    "Graph requires matplotlib. Install it with: pip install matplotlib"
                    if lang == "en"
                    else "Graf kræver matplotlib. Installer det fx med: pip install matplotlib"
                )
                return

            position = internal_position(self.position_var.get())
            stats = POSITION_STATS[position]

            ages = []
            ratings = []
            averages = []
            days = []

            for result in self.last_history:
                if result.rating is None:
                    continue
                days.append(result.day)
                ages.append(result.age)
                ratings.append(result.rating)
                averages.append(sum(result.after_stats[stat] for stat in stats) / len(stats))

            if not ages:
                raise ValueError(
                    "There are no ratings in the history."
                    if lang == "en"
                    else "Der er ingen vurderingstal i historikken."
                )

            history_without_penalty = self._simulate_history_without_penalty()
            no_penalty_average_by_day = {
                result.day: sum(result.after_stats[stat] for stat in stats) / len(stats)
                for result in history_without_penalty
                if result.rating is not None
            }

            averages_without_penalty = [
                no_penalty_average_by_day.get(day)
                for day in days
            ]

            if lang == "en":
                rating_label = "Rating"
                average_label = "Average"
                average_no_penalty_label = "Average without penalty"
                age_label = "Age"
                level_label = "Level"
                title = f"Development — {self._display_position(position)}"
            else:
                rating_label = "Vurderingstal"
                average_label = "Gennemsnit"
                average_no_penalty_label = "Gennemsnit uden straf"
                age_label = "Alder"
                level_label = "Niveau"
                title = f"Udvikling — {self._display_position(position)}"

            plt.figure()
            plt.plot(ages, ratings, label=rating_label)
            plt.plot(ages, averages, label=average_label)

            if averages_without_penalty and all(value is not None for value in averages_without_penalty):
                plt.plot(ages, averages_without_penalty, label=average_no_penalty_label)

            plt.xlabel(age_label)
            plt.ylabel(level_label)
            plt.title(title)
            plt.grid(True)
            plt.legend()
            plt.show()

        except Exception as e:
            self._main_notice(str(e))


    def _days_label(self, days):
        return ("1 day" if int(days) == 1 else f"{int(days)} days") if self._language_code() == "en" else ("1 dag" if int(days) == 1 else f"{int(days)} dage")

    def _wrap_report_text(self, text, width=92, subsequent_indent=""):
        wrapped = textwrap.wrap(
            str(text),
            width=width,
            subsequent_indent=subsequent_indent,
            break_long_words=False,
            break_on_hyphens=False,
        )
        return wrapped or [""]

    def _phase_distribution_report_lines(self, phase, indent="      ", width=92):
        label = "Distribution" if self._language_code() == "en" else "Fordeling"
        if phase.exercise == "Træningskamp":
            text = "automatically distributed equally across the position's abilities" if self._language_code() == "en" else "automatisk ligeligt fordelt på positionens stats"
            return [f"{indent}{label}: {text}"]

        distribution = phase.distribution or {}
        if not distribution:
            text = "not specified" if self._language_code() == "en" else "ikke angivet"
            return [f"{indent}{label}: {text}"]

        text = f"{label}: " + ", ".join(f"{self._display_stat(stat)}: {value}" for stat, value in distribution.items())
        return self._wrap_report_text(
            indent + text,
            width=width,
            subsequent_indent=indent + " " * len(label + ": "),
        )

    def _phase_summary_for_report(self, phase):
        intensity = self._display_intensity(INTENSITY_LABELS_BY_POINTS.get(phase.training_points, str(phase.training_points)))
        return f"{self._display_exercise(phase.exercise)}: {self._days_label(phase.days)} - {intensity}"

    def _program_item_to_report_lines(self, item, number):
        lines = []

        if isinstance(item, TrainingCycle):
            lines.append(
                f"{number}. {item.name}: {self._days_label(item.days)} "
                f"({item.repetitions} gentagelser x {self._days_label(item.base_days)})"
            )
            lines.append("   Plan pr. gentagelse:")

            for phase_index, phase in enumerate(item.phases, start=1):
                lines.append(f"   {number}.{phase_index} {self._phase_summary_for_report(phase)}")
                lines.extend(self._phase_distribution_report_lines(phase, indent="       "))

            return lines

        lines.append(f"{number}. {self._phase_summary_for_report(item)}")
        lines.extend(self._phase_distribution_report_lines(item, indent="   "))
        return lines

    def _build_report_lines(self):
        if not self.last_history or self.last_summary is None:
            raise ValueError("Der er ingen rapportdata endnu. Kør en simulering først.")

        position = internal_position(self.position_var.get())
        display_pos = self._display_position(position)
        start_age = float(self.start_age_var.get())
        xp = float(self.xp_var.get())
        total_days = sum(item.days for item in self.program)
        end_age = calculate_end_age(start_age, total_days)
        summary = self.last_summary

        lines = []
        lines.append("VMAN Training Planner - træningsrapport")
        lines.append("=" * 32)
        lines.append("")
        lines.append(f"Position: {display_pos}")
        lines.append(f"Periode: {start_age:.2f} -> {end_age:.2f}")
        lines.append(f"Programdage: {total_days}")
        lines.append(f"XP: {xp:.2f}")
        lines.append(f"XP ved: {self._display_intensity(internal_intensity(self.xp_reference_intensity_var.get()))}")

        ebr_enabled, ebr_ratio, ebr_knee = self._get_experience_bonus_settings()
        if ebr_enabled:
            knee_label = {"early": "tidlig", "linear": "lineær", "late": "sen"}.get(ebr_knee, "lineær")
            lines.append(f"Erfaringsbonus: aktiv - ratio {ebr_ratio:.2f} - gradient {knee_label}")
        else:
            lines.append("Erfaringsbonus: slået fra")

        lines.append("")
        lines.append("Træningsprogram")
        lines.append("-" * 15)

        if self.program:
            for i, item in enumerate(self.program, start=1):
                lines.extend(self._program_item_to_report_lines(item, i))
                lines.append("")
        else:
            lines.append("Ingen sessions.")

        lines.append("")
        lines.append("Slutstats")
        lines.append("-" * 9)

        for stat in POSITION_STATS[position]:
            lines.append(f"{stat}: {summary[stat]:.2f}")

        lines.append("")
        lines.append(f"Gennemsnit: {summary['Gennemsnit']:.2f}")
        lines.append(f"Vurderingstal ({display_pos}): {summary[f'Vurderingstal_{position}']:.2f}")

        return lines

    def _write_simple_pdf(self, path, lines):
        # Lille indbygget PDF-generator, så brugeren ikke skal installere ekstra pakker.
        # Bruger WinAnsiEncoding/cp1252, så danske tegn som ÆØÅ/æøå virker korrekt.
        def pdf_escape(text):
            raw = str(text).encode("cp1252", errors="replace")
            raw = raw.replace(b"\\", b"\\\\")
            raw = raw.replace(b"(", b"\\(")
            raw = raw.replace(b")", b"\\)")
            return raw

        width, height = 595, 842  # A4 points
        left = 50
        top = 790
        line_height = 13
        max_lines_per_page = int((top - 50) / line_height)

        pages = []
        for start in range(0, len(lines), max_lines_per_page):
            pages.append(lines[start:start + max_lines_per_page])

        objects = []
        pages_kids = []

        # 1 Catalog, 2 Pages, 3 Font.
        # WinAnsiEncoding er vigtig for danske tegn i standard PDF-fonten.
        objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
        objects.append(b"")  # placeholder
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")

        for page_lines in pages:
            content_parts = [b"BT", b"/F1 10 Tf", f"{left} {top} Td".encode("ascii")]
            first = True
            for line in page_lines:
                if first:
                    first = False
                else:
                    content_parts.append(f"0 -{line_height} Td".encode("ascii"))
                content_parts.append(b"(" + pdf_escape(line) + b") Tj")
            content_parts.append(b"ET")
            content = b"\n".join(content_parts)

            content_obj_num = len(objects) + 1
            objects.append(
                f"<< /Length {len(content)} >>\nstream\n".encode("ascii")
                + content
                + b"\nendstream"
            )

            page_obj_num = len(objects) + 1
            objects.append(
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_obj_num} 0 R >>"
                ).encode("ascii")
            )
            pages_kids.append(f"{page_obj_num} 0 R")

        objects[1] = f"<< /Type /Pages /Kids [{' '.join(pages_kids)}] /Count {len(pages_kids)} >>".encode("ascii")

        pdf = bytearray()
        pdf.extend(b"%PDF-1.4\n")
        offsets = [0]

        for i, obj in enumerate(objects, start=1):
            offsets.append(len(pdf))
            pdf.extend(f"{i} 0 obj\n".encode("ascii"))
            pdf.extend(obj)
            pdf.extend(b"\nendobj\n")

        xref_offset = len(pdf)
        pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        pdf.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))

        pdf.extend(
            (
                "trailer\n"
                f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
                "startxref\n"
                f"{xref_offset}\n"
                "%%EOF\n"
            ).encode("ascii")
        )

        with open(path, "wb") as f:
            f.write(pdf)

    def _export_report_pdf(self):
        try:
            lines = self._build_report_lines()
            path = filedialog.asksaveasfilename(
                defaultextension=".pdf",
                filetypes=[("PDF", "*.pdf")],
                title="Eksportér rapport som PDF",
            )
            if not path:
                return

            self._write_simple_pdf(path, lines)

        except Exception as e:
            self._main_notice(str(e))

    def _rtf_escape(self, text):
        text = str(text)
        output = []
        for ch in text:
            code = ord(ch)
            if ch == "\\":
                output.append("\\\\")
            elif ch == "{":
                output.append("\\{")
            elif ch == "}":
                output.append("\\}")
            elif code > 127:
                if code > 32767:
                    code -= 65536
                output.append(f"\\u{code}?")
            else:
                output.append(ch)
        return "".join(output)

    def _export_report_doc(self):
        try:
            lines = self._build_report_lines()
            path = filedialog.asksaveasfilename(
                defaultextension=".doc",
                filetypes=[("Word-kompatibel DOC", "*.doc"), ("RTF", "*.rtf")],
                title="Eksportér rapport som DOC",
            )
            if not path:
                return

            rtf_lines = [
                r"{\rtf1\ansi\deff0",
                r"{\fonttbl{\f0 Helvetica;}}",
                r"\fs22",
            ]

            for line in lines:
                if line.startswith("VMAN Training Planner"):
                    rtf_lines.append(r"\b " + self._rtf_escape(line) + r"\b0\par")
                elif line in {"Træningsprogram", "Slutstats"}:
                    rtf_lines.append(r"\par\b " + self._rtf_escape(line) + r"\b0\par")
                elif set(line) <= {"=", "-"} and line:
                    continue
                elif line == "":
                    rtf_lines.append(r"\par")
                else:
                    rtf_lines.append(self._rtf_escape(line) + r"\par")

            rtf_lines.append("}")

            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(rtf_lines))

        except Exception as e:
            self._main_notice(str(e))


    def _csv_number(self, value, decimals=2):
        try:
            value = float(value)
        except Exception:
            return value

        rounded = round(value, decimals)

        # Maks 2 decimaler, men hele tal får én decimal.
        # Det forhindrer at regneark viser fx 57 anderledes end 57.1.
        if abs(rounded - round(rounded)) < 1e-9:
            return f"{rounded:.1f}"

        text = f"{rounded:.{decimals}f}"
        text = text.rstrip("0").rstrip(".")
        return text if text else "0.0"


    def _export_history_csv(self):
        try:
            if not self.last_history:
                raise ValueError("Der er ingen historik endnu. Kør en simulering først.")

            path = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV", "*.csv")],
                title="Eksportér historik",
            )
            if not path:
                return

            position = internal_position(self.position_var.get())
            stats = POSITION_STATS[position]
            fieldnames = ["day", "age", "exercise", "intensity", "rating", "average"] + [f"stat_{s}" for s in stats]

            with open(path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()

                for result in self.last_history:
                    intensity = INTENSITY_LABELS_BY_POINTS.get(result.training_points, str(result.training_points))
                    average = sum(result.after_stats[stat] for stat in stats) / len(stats)
                    row = {
                        "day": int(result.day),
                        "age": self._csv_number(result.age),
                        "exercise": result.exercise,
                        "intensity": intensity,
                        "rating": "" if result.rating is None else self._csv_number(result.rating),
                        "average": self._csv_number(average),
                    }

                    for stat in stats:
                        row[f"stat_{stat}"] = self._csv_number(result.after_stats[stat])

                    writer.writerow(row)

        except Exception as e:
            self._main_notice(str(e))



def run_app():
    app = VmanApp()
    app.mainloop()
