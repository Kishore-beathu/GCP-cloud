/* Quiz bank — organised by topic. Each question has an explanation aligned with the modules. */

window.QUIZZES = {
  gamp5: {
    title: "GAMP 5 (2nd edition) — Concepts",
    questions: [
      {
        q: "Which of the following best describes the central new theme introduced in GAMP 5 2nd edition (2022)?",
        options: [
          "Mandatory use of the V-model for every regulated system.",
          "Critical thinking applied throughout the system life cycle.",
          "Replacement of supplier audits with self-declarations only.",
          "Removal of risk management for low-impact systems.",
        ],
        a: 1,
        explain: "GAMP 5 2nd edition makes critical thinking an explicit, central concept. Iterative life cycles are now also recognised, but the V-model is not mandated.",
      },
      {
        q: "A LIMS used for QC release testing is being configured with custom workflows and a small bespoke calculation module. Which classification approach is most appropriate?",
        options: [
          "Treat the whole system as Category 3 (non-configured).",
          "Treat the configured workflows as Category 4 and the bespoke calculation as Category 5; address as a hybrid.",
          "Treat the whole system as Category 1 since the database is infrastructure.",
          "Skip classification; rely solely on the supplier's certificate.",
        ],
        a: 1,
        explain: "Most real systems are hybrids. Configured COTS = Category 4; bespoke code = Category 5; underlying OS/DB = Category 1. Apply the right approach to each component.",
      },
      {
        q: "Which control type sits highest in the GAMP 5 hierarchy of controls?",
        options: [
          "Procedural SOP-based control.",
          "Detection and recovery control after an event.",
          "Engineered design control that removes the risk.",
          "Periodic management review.",
        ],
        a: 2,
        explain: "Design out the risk where possible; configure where you can't design out; SOPs and detection are the lower tiers.",
      },
      {
        q: "Which life-cycle deliverable confirms the system is installed correctly on the qualified infrastructure?",
        options: [
          "PQ — Performance Qualification.",
          "OQ — Operational Qualification.",
          "IQ — Installation Qualification.",
          "URS — User Requirements Specification.",
        ],
        a: 2,
        explain: "IQ verifies installation; OQ verifies functions work; PQ verifies the system performs in the real process.",
      },
      {
        q: "An iterative / Agile delivery for a GxP system is acceptable provided that:",
        options: [
          "Documentation is removed and replaced by working software.",
          "Validation activities are produced incrementally and a formal release qualification is performed before each production release.",
          "The Product Owner replaces the QA function entirely.",
          "Only the latest sprint's tests are retained as evidence.",
        ],
        a: 1,
        explain: "Agile is compatible with CSV if specifications, traceability, testing, evidence and a formal release gate are kept — produced incrementally.",
      },
      {
        q: "What is the primary purpose of leveraging supplier documentation?",
        options: [
          "To avoid documenting requirements altogether.",
          "To reuse fit-for-purpose evidence and focus regulated-user effort where it adds value.",
          "To transfer regulatory accountability to the supplier.",
          "To skip risk assessment for COTS products.",
        ],
        a: 1,
        explain: "Leveraging avoids duplication. Regulatory accountability remains with the regulated user; supplier evidence must be assessed and mapped.",
      },
      {
        q: "Which of these is NOT one of the ALCOA+ attributes?",
        options: ["Attributable", "Contemporaneous", "Compressed", "Enduring"],
        a: 2,
        explain: "The attributes are Attributable, Legible, Contemporaneous, Original, Accurate, Complete, Consistent, Enduring, Available. 'Compressed' is not one of them.",
      },
      {
        q: "For a continuously updated SaaS GxP application, which operational control is most important to add?",
        options: [
          "An on-premise mirror of the application.",
          "A change-notification process and regression test suite executed before vendor updates reach production.",
          "Disabling all vendor updates indefinitely.",
          "Replacing the supplier with an open-source equivalent.",
        ],
        a: 1,
        explain: "SaaS continuous delivery requires advance change notification, a sandbox/test tenant and a regression suite to maintain validated state.",
      },
    ],
  },

  annex11: {
    title: "EU GMP Annex 11",
    questions: [
      {
        q: "Per Annex 11 §7, backup data must be:",
        options: [
          "Stored on the same physical disk as the production data for performance.",
          "Made at periodic intervals; integrity, accuracy and ability to restore checked during validation and monitored periodically.",
          "Only required for systems handling clinical data.",
          "Verified once at go-live and never tested again.",
        ],
        a: 1,
        explain: "Annex 11 §7 requires periodic backups whose integrity and restore capability are verified during validation and monitored periodically.",
      },
      {
        q: "Annex 11 §9 (Audit Trails) requires that audit trails be:",
        options: [
          "Hidden from end-users by default.",
          "Available, convertible to a generally intelligible form, and regularly reviewed.",
          "Generated only after critical incidents.",
          "Retained for at most one year.",
        ],
        a: 1,
        explain: "Audit trails must be available, intelligible and reviewed regularly. Retention follows the predicate record retention.",
      },
      {
        q: "Which Annex 11 section addresses periodic evaluation of the validated state?",
        options: ["§5 Data", "§9 Audit Trails", "§11 Periodic Evaluation", "§16 Business Continuity"],
        a: 2,
        explain: "§11 requires periodic evaluation including functionality, deviations, incidents, upgrades, performance, reliability, security and validation status.",
      },
      {
        q: "Annex 11 §6 on Accuracy Checks applies to:",
        options: [
          "All data entered into any system.",
          "Critical data entered manually — additional check by a second person or by validated electronic means.",
          "Only automated data feeds.",
          "Data displayed but not stored.",
        ],
        a: 1,
        explain: "§6 targets critical manually entered data; the additional check is risk-based and proportionate.",
      },
      {
        q: "For batch certification, Annex 11 §15 expects:",
        options: [
          "Any operator with system access can certify a batch.",
          "Only Qualified Persons may certify; identification and recording via electronic signature.",
          "Certification by paper printout only.",
          "An external regulator signs each batch.",
        ],
        a: 1,
        explain: "§15 reserves batch certification to QPs, recorded with an electronic signature that clearly identifies the QP.",
      },
      {
        q: "Annex 11 §3 (Suppliers and Service Providers) effectively treats internal IT as:",
        options: [
          "Outside the scope of CSV.",
          "Equivalent to an external supplier requiring formal agreements and competence assessment.",
          "Exempt from quality agreements.",
          "Only relevant for clinical systems.",
        ],
        a: 1,
        explain: "§3 explicitly considers IT departments analogous to suppliers — formal agreements, risk-based assessment and defined responsibilities apply.",
      },
    ],
  },

  part11: {
    title: "21 CFR Part 11",
    questions: [
      {
        q: "Per §11.10(e), audit trails must be:",
        options: [
          "Optional for non-critical records.",
          "Secure, computer-generated, time-stamped and retained at least as long as the underlying records.",
          "Editable by system administrators to correct errors.",
          "Maintained on paper printouts only.",
        ],
        a: 1,
        explain: "§11.10(e) requires secure, computer-generated, time-stamped audit trails. Record changes must not obscure prior values, and the audit trail must be retained as long as the subject records.",
      },
      {
        q: "Under §11.200(a), a non-biometric electronic signature must include at least:",
        options: [
          "One identification component such as a username only.",
          "Two distinct identification components such as an ID code and password.",
          "A government-issued ID number.",
          "A pre-printed paper signature.",
        ],
        a: 1,
        explain: "Non-biometric e-signatures need at least two distinct components. After the first signing in a continuous session at least one component must be re-entered for subsequent signings.",
      },
      {
        q: "§11.50 (Signature Manifestations) requires the signed record to clearly indicate:",
        options: [
          "Only the signer's printed name.",
          "Printed name of signer, date and time of signature, and the meaning of the signature (review/approval/responsibility/authorship).",
          "The IP address from which the signature was applied.",
          "The signer's home address.",
        ],
        a: 1,
        explain: "§11.50 requires printed name, timestamp and the meaning of the signature, included on any human-readable form (display or printout).",
      },
      {
        q: "Which of the following defines a 'closed system' under §11.3?",
        options: [
          "A system disconnected from the internet.",
          "A system whose access is controlled by persons responsible for the content of the electronic records on it.",
          "A read-only archive.",
          "A system without electronic signatures.",
        ],
        a: 1,
        explain: "A closed system is defined by who controls access — persons responsible for the records. Open systems require additional controls like encryption and digital signatures (§11.30).",
      },
      {
        q: "Which §11.10 control requires the ability to generate accurate and complete copies of electronic records suitable for FDA inspection?",
        options: [
          "§11.10(a)",
          "§11.10(b)",
          "§11.10(d)",
          "§11.10(i)",
        ],
        a: 1,
        explain: "§11.10(b) requires accurate and complete copies in both human-readable and electronic form. This is a frequent inspection focus.",
      },
      {
        q: "Per §11.100(b), before an organization sanctions an individual's electronic signature, it must:",
        options: [
          "Issue a temporary password.",
          "Verify the identity of the individual.",
          "Train the individual on Part 11.",
          "Notify the FDA in advance.",
        ],
        a: 1,
        explain: "Identity verification before issuing signing credentials is a foundational control under §11.100(b).",
      },
      {
        q: "The FDA's 2003 Scope and Application guidance introduced:",
        options: [
          "Removal of all Part 11 requirements.",
          "Enforcement discretion for some elements, focusing day-to-day enforcement on records required by predicate rules.",
          "A new mandate to validate every spreadsheet.",
          "A requirement to submit all electronic records to the FDA monthly.",
        ],
        a: 1,
        explain: "Enforcement discretion narrows day-to-day enforcement, but core obligations (audit trails, validation, accurate copies) remain.",
      },
    ],
  },

  mixed: {
    title: "Mixed CSV scenarios",
    questions: [
      {
        q: "A spreadsheet with macros calculates assay potency that is used in batch release. Under GAMP 5 it is best classified as:",
        options: [
          "Category 1 (infrastructure).",
          "Category 3 (non-configured).",
          "Category 5 (custom) for the macro logic; the underlying spreadsheet engine is Category 1.",
          "Out of scope as it is just a spreadsheet.",
        ],
        a: 2,
        explain: "Macros that perform GxP calculations are bespoke logic = Category 5. The spreadsheet engine itself is Category 1. Validate the calculation, lock the template, control versions and access.",
      },
      {
        q: "During an inspection, the inspector asks to see proof that the system performs as required. Which document is the inspector most likely to start with?",
        options: [
          "The Validation Summary Report and Traceability Matrix.",
          "The supplier's marketing brochure.",
          "The OS patch list.",
          "The disaster recovery contract.",
        ],
        a: 0,
        explain: "VSR + RTM is the spine — it lets the inspector trace any URS line to evidence. Other documents are referenced from there.",
      },
      {
        q: "Generic / shared user accounts used for GxP signing are:",
        options: [
          "Acceptable if the password is complex.",
          "Acceptable for read-only access.",
          "Non-compliant with both Annex 11 §12 and Part 11 §11.100(a) — signatures must be unique and attributable.",
          "Acceptable in development only.",
        ],
        a: 2,
        explain: "Signatures must be unique to one individual (Part 11 §11.100(a)) and identity-attributable (Annex 11 §12). Shared GxP accounts are a frequent finding.",
      },
      {
        q: "You receive a vendor patch notification three days before automatic deployment to your SaaS QMS. Best response?",
        options: [
          "Allow automatic deployment; act if anything breaks.",
          "Trigger your change-impact assessment, run the agreed regression suite in the sandbox, document the assessment, then accept or defer the patch per Quality Agreement.",
          "Disable the SaaS and migrate to paper.",
          "Refuse the patch indefinitely.",
        ],
        a: 1,
        explain: "Continuous-update SaaS needs an established process: notification window, sandbox test, documented impact assessment and Quality Agreement terms.",
      },
      {
        q: "A migration moves 12 months of stability data from a legacy LIMS to a new one. Minimum validation activities should include:",
        options: [
          "A signed email confirming the migration ran.",
          "Migration plan, mapping spec, pilot migration, reconciliation (counts, checksums, sample content), cutover and migration report.",
          "Only a backup of the legacy database.",
          "Nothing — migrations are out of CSV scope.",
        ],
        a: 1,
        explain: "Migrating GxP data is a GxP activity. Reconciliation is the critical evidence — counts, checksums and sampled content review.",
      },
      {
        q: "Locally adaptive ML models that retrain on production data and immediately change behaviour are, for high-risk GxP decisions, generally:",
        options: [
          "Encouraged because they improve accuracy automatically.",
          "Discouraged today — most regulated deployments use locked models with controlled retraining and re-qualification.",
          "Exempt from validation.",
          "The same as deterministic software.",
        ],
        a: 1,
        explain: "Continuously learning models in production for high-risk GxP decisions remain hard to validate. Locked models, periodically retrained under change control, are the safer pattern today.",
      },
    ],
  },
};
