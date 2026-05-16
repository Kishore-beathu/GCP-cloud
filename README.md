# CSV Academy — Computer System Validation Learning Tool

An interactive, self-contained web app for learning Computer System Validation (CSV)
based on **ISPE GAMP&reg; 5 (2nd edition, 2022)**, **EU GMP Annex 11** and **21 CFR Part 11**.

> Educational reference only. Not a substitute for the official ISPE guide,
> EudraLex Volume 4 Annex 11, or 21 CFR Part 11.

## What's inside

| Section | Coverage |
| --- | --- |
| **GAMP 5 modules** | 20 modules: Intro, GxP & ALCOA+, Quality Risk Management, Software Categories (1/3/4/5), Life cycle, Specifications & RTM, IQ/OQ/PQ, Suppliers, Change & Configuration Management, Operation, Retirement, Critical Thinking, Agile, IT Infrastructure & Services, Cloud/SaaS, AI/ML, Data Integrity by Design, Electronic Signatures, Deliverables, Inspections |
| **EU Annex 11** | Principle plus all 17 numbered sections (Risk Mgmt, Personnel, Suppliers, Validation, Data, Accuracy, Storage, Printouts, Audit Trails, Change Mgmt, Periodic Eval., Security, Incident Mgmt, E-Signature, Batch Release, BCP, Archiving) |
| **21 CFR Part 11** | Subpart A (Scope, Implementation, Definitions), Subpart B (§11.10, §11.30, §11.50, §11.70), Subpart C (§11.100, §11.200, §11.300) |
| **Comparison matrix** | Each CSV topic cross-referenced across GAMP 5, Annex 11, Part 11 |
| **Quizzes** | 4 banks (GAMP 5, Annex 11, Part 11, Mixed scenarios) with explanations; best scores saved in your browser |
| **Glossary** | 50+ CSV terms, tagged by framework and topic, searchable |

## Run it

Pure static site — no build step, no dependencies. Just open `index.html` in a browser:

```bash
# from the project root
python3 -m http.server 8000
# then visit http://localhost:8000
```

Or open `index.html` directly via the file URL.

## File layout

```
index.html                  # app shell
assets/css/styles.css       # styling
assets/js/app.js            # SPA router + quiz engine
assets/js/data/
  modules.js                # GAMP 5 module content
  annex11.js                # Annex 11 section content
  part11.js                 # 21 CFR Part 11 section content
  comparison.js             # cross-reference matrix
  quizzes.js                # quiz banks
  glossary.js               # glossary terms
```

## Progress tracking

The app stores which sections you've visited and your best quiz scores in `localStorage`
under the key `csv-academy-state-v1`. Clear browser storage to reset.
