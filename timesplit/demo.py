"""A synthetic workday, for seeing the whole app without a Windows machine.

Deliberately messy: two jobs interleaved, a coffee break, a lunch lock, some
windows the seed rules will not recognise, and a stretch of flicking between
things. That is what makes the dashboard worth looking at.
"""

from __future__ import annotations


def demo_script() -> list[dict]:
    return [
        # Morning: school admin.
        {"exe": "outlook.exe", "title": "Inbox - Outlook", "duration_s": 900},
        {"exe": "chrome.exe", "title": "Canvas Gradebook - BIO 101 - Google Chrome",
         "url": "https://canvas.instructure.com/courses/1/gradebook", "duration_s": 2400},
        {"exe": "chrome.exe", "title": "Attendance - PowerSchool - Google Chrome",
         "url": "https://powerschool.com/attendance", "duration_s": 1200},
        {"exe": "word.exe", "title": "Lesson plan week 9.docx - Word", "duration_s": 1800},

        # Coffee: the machine sits idle for eleven minutes.
        {"exe": "word.exe", "title": "Lesson plan week 9.docx - Word",
         "duration_s": 660, "idle_ms": 660_000},

        {"exe": "excel.exe", "title": "Student roster.xlsx - Excel", "duration_s": 1500},

        # Switching over to the real estate side.
        {"exe": "comet.exe", "title": "MLS search - Comet",
         "url": "https://matrix.mlsmatrix.com/Matrix/Search", "duration_s": 1800},
        {"exe": "chrome.exe", "title": "12 Oak St - Zillow - Google Chrome",
         "url": "https://zillow.com/homedetails/12-oak-st", "duration_s": 1200},
        {"exe": "explorer.exe", "title": "Downloads", "duration_s": 4},
        {"exe": "chrome.exe", "title": "Purchase agreement - dotloop - Google Chrome",
         "url": "https://dotloop.com/my/loops/44821", "duration_s": 2700},

        # Lunch: screen locked for 40 minutes.
        {"exe": "", "title": "", "duration_s": 2400, "locked": True},

        {"exe": "quickbooks.exe", "title": "Burnett Group LLC - Chart of Accounts",
         "duration_s": 2100},
        {"exe": "chrome.exe", "title": "Closing disclosure review - Qualia - Google Chrome",
         "url": "https://qualiasystems.com/orders/7781", "duration_s": 1800},
        {"exe": "acrobat.exe", "title": "Settlement statement 12 Oak.pdf - Adobe Acrobat",
         "duration_s": 1500},

        # Back to school work late in the day.
        {"exe": "chrome.exe", "title": "Canvas Announcements - Google Chrome",
         "url": "https://canvas.instructure.com/courses/1/announcements", "duration_s": 900},
        {"exe": "zoom.exe", "title": "Parent conference - Zoom Meeting", "duration_s": 1800},
        {"exe": "spotify.exe", "title": "Discover Weekly - Spotify", "duration_s": 600},
        {"exe": "outlook.exe", "title": "Inbox - Outlook", "duration_s": 1200},
    ]
