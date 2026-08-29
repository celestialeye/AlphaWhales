import os


FUND_MANAGERS = [
    # --- Value ---
    {
        "group": "Value",
        "cik": "0001697868",
        "name": "Valley Forge Capital",
        "manager": "Kantesaria",
        "annotation": "5Y CAGR 32.6%; 9 holdings"
    },
    {
        "group": "Value",
        "cik": "0001115373",
        "name": "Semper Augustus",
        "manager": "Bloomstran",
        "annotation": "5Y CAGR 30.6%; 39 holdings"
    },
    {
        "group": "Value",
        "cik": "0001709323",
        "name": "Himalaya Capital",
        "manager": "Li Lu",
        "annotation": "5Y CAGR 14.5%; 9 holdings"
    },
    {
        "group": "Value",
        "cik": "0001549575",
        "name": "Dalal Street",
        "manager": "Pabrai",
        "annotation": "5Y CAGR 14.3%; 4 holdings"
    },
    {
        "group": "Value",
        "cik": "0001641864",
        "name": "Giverny Capital",
        "manager": "Rochon",
        "annotation": "5Y CAGR 18.1%; 50 holdings"
    },
    {
        "group": "Value",
        "cik": "0001631014",
        "name": "AltaRock Partners",
        "manager": "Massey",
        "annotation": "5Y CAGR 16.4%; 8 holdings"
    },
    {
        "group": "Value",
        "cik": "0000813917",
        "name": "Harris Associates / Oakmark",
        "manager": "Nygren",
        "annotation": "5Y CAGR 14.5%; 24 holdings"
    },
    {
        "group": "Value",
        "cik": "0001647251",
        "name": "TCI Fund Management",
        "manager": "Hohn",
        "annotation": "5Y CAGR 12.0%; 9 holdings; $53B"
    },
    {
        "group": "Value",
        "cik": "0001112520",
        "name": "Akre Capital Management",
        "manager": "Akre",
        "annotation": "Compounder; 18 holdings"
    },
    {
        "group": "Value",
        "cik": "0001167483",
        "name": "Baupost Group",
        "manager": "Klarman",
        "annotation": "Deep-value specialist; 22 holdings; $5B"
    },
    {
        "group": "Value",
        "cik": "0001671657",
        "name": "Dorsey Asset Management",
        "manager": "Dorsey",
        "annotation": "Moat-focused; 10 holdings"
    },
    {
        "group": "Value",
        "cik": "0001067983",
        "name": "Berkshire Hathaway",
        "manager": "Buffett",
        "annotation": "42 holdings; $274B"
    },

    # --- High-performance concentrated ---
    {
        "group": "High-performance concentrated",
        "cik": "0001697591",
        "name": "CAS Investment Partners",
        "manager": "Sosin",
        "annotation": "3Y 102% annualized; 0% turnover; 5 holdings"
    },
    {
        "group": "High-performance concentrated",
        "cik": "0001777813",
        "name": "Atreides Management",
        "manager": "Baker",
        "annotation": "3Y 54% annualized; growth-at-value"
    },
    {
        "group": "High-performance concentrated",
        "cik": "0001387322",
        "name": "Whale Rock Capital",
        "manager": "Sacerdote",
        "annotation": "3Y 54% annualized; concentrated technology"
    },
    {
        "group": "High-performance concentrated",
        "cik": "0002026053",
        "historical_ciks": ["0001336528"],
        "name": "Pershing Square",
        "manager": "Ackman",
        "annotation": "Concentrated activist; 15 holdings"
    },

    # --- Quality compounder ---
    {
        "group": "Quality compounder",
        "cik": "0001798849",
        "name": "Durable Capital Partners",
        "manager": "Ellenbogen",
        "annotation": "Quality growth; 40 holdings; $10B"
    },
    {
        "group": "Quality compounder",
        "cik": "0001553733",
        "name": "Brave Warrior Advisors",
        "manager": "Greenberg",
        "annotation": "Deep value; 33 holdings; $4B"
    },
    {
        "group": "Quality compounder",
        "cik": "0001427119",
        "name": "Meritage Group",
        "manager": "Simons",
        "annotation": "Concentrated; 10 holdings; $3B"
    },
    {
        "group": "Quality compounder",
        "cik": "0001766908",
        "name": "ShawSpring Partners",
        "manager": "Hong",
        "annotation": "Concentrated quality; 11 holdings"
    },

    # --- 2026 expansion ---
    {
        "group": "2026 expansion",
        "cik": "0001569205",
        "name": "Fundsmith LLP",
        "manager": "Terry Smith",
        "annotation": "UK quality compounder; LEGACY_UNVERIFIED HQ 0.90; tenure 8.0Q"
    },
    {
        "group": "2026 expansion",
        "cik": "0001107310",
        "name": "Eminence Capital",
        "manager": "Sandler",
        "annotation": "Concentrated value; LEGACY_UNVERIFIED HQ 0.74; tenure 6.0Q"
    },
    {
        "group": "2026 expansion",
        "cik": "0001034524",
        "name": "Polen Capital",
        "manager": "Polen Focus Growth",
        "annotation": "Growth-quality; LEGACY_UNVERIFIED HQ 0.54; tenure 3.0Q"
    },
    {
        "group": "2026 expansion",
        "cik": "0001135730",
        "name": "Coatue Management",
        "manager": "Laffont",
        "annotation": "Technology crossover; LEGACY_UNVERIFIED HQ 0.49; tenure 2.5Q"
    },
    {
        "group": "2026 expansion",
        "cik": "0001103804",
        "name": "Viking Global Investors",
        "manager": "Halvorsen",
        "annotation": "Quality-oriented; LEGACY_UNVERIFIED HQ 0.45; tenure 4.0Q"
    },
    {
        "group": "2026 expansion",
        "cik": "0001061165",
        "name": "Lone Pine Capital",
        "manager": "Mandel",
        "annotation": "Long/short; LEGACY_UNVERIFIED HQ 0.40; tenure 2.0Q"
    },
]

import os

SEC_IDENTITY = os.environ.get(
    "EDGAR_IDENTITY",
    "Sec13F Dashboard admin@sec13f.local",
)
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
CACHE_TTL_HOURS = 6
