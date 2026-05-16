/* GAMP 5 (2nd edition, 2022) learning modules.
   Content is paraphrased educational reference — not a substitute for the official ISPE guide. */

window.GAMP5_MODULES = [
  {
    id: "intro",
    title: "1. Introduction to CSV & GAMP 5",
    summary: "What CSV is, why it exists, and what changed in GAMP 5 2nd edition.",
    sections: [
      {
        h: "What is Computer System Validation?",
        body: `
          <p><strong>Computer System Validation (CSV)</strong> is the documented process of demonstrating that a
          computerized system used in a GxP regulated activity is fit for its intended use, consistently produces
          results meeting predetermined specifications, and protects product quality and patient safety.</p>
          <p>A "computerized system" is the combination of hardware, software, peripheral devices, networks,
          documentation, personnel, and the controlled function or process it supports.</p>
          <div class="callout"><strong>Regulatory drivers:</strong> EU GMP Annex 11, US 21 CFR Part 11,
          PIC/S PI 041, ICH Q9 (Quality Risk Management), MHRA / FDA data integrity guidance, and the ISPE GAMP&reg;
          5 Guide.</div>
        `,
      },
      {
        h: "Why GAMP 5?",
        body: `
          <p>GAMP 5 provides a <strong>risk-based, scalable framework</strong> for delivering and operating GxP
          computerized systems. It translates regulatory expectations into practical life-cycle activities.</p>
          <ul>
            <li>Focuses validation effort on patient safety, product quality, and data integrity.</li>
            <li>Scales activities to the system's complexity, novelty, and impact.</li>
            <li>Encourages leveraging supplier activities and documentation.</li>
            <li>Promotes <em>critical thinking</em> over check-box compliance.</li>
          </ul>
        `,
      },
      {
        h: "What changed in the 2nd edition (2022)?",
        body: `
          <div class="table-wrap"><table class="styled">
            <thead><tr><th>Theme</th><th>2nd edition emphasis</th></tr></thead>
            <tbody>
              <tr><td>Critical thinking</td><td>Now an explicit, central concept — applied throughout the life cycle.</td></tr>
              <tr><td>Iterative / Agile</td><td>Recognised as a valid life-cycle model alongside linear (V-model).</td></tr>
              <tr><td>Software tools</td><td>Guidance on validating development, testing and infrastructure tools.</td></tr>
              <tr><td>IT services & infrastructure</td><td>Qualified infrastructure as a platform; service management (ITIL-aligned).</td></tr>
              <tr><td>Cloud / SaaS</td><td>Dedicated appendix (D8) for cloud-hosted GxP systems.</td></tr>
              <tr><td>AI / Machine learning</td><td>Risk approach for systems that learn or adapt over time.</td></tr>
              <tr><td>Blockchain & open source</td><td>Recognised as legitimate technologies under risk-based control.</td></tr>
              <tr><td>Data integrity by design</td><td>ALCOA+ baked into specifications and design, not bolted on.</td></tr>
              <tr><td>Lean / right-sized</td><td>Avoiding duplication of supplier work, fit-for-purpose documentation.</td></tr>
            </tbody>
          </table></div>
        `,
      },
      {
        h: "Core principles",
        body: `
          <ol>
            <li><strong>Product and process understanding</strong> — know what the system must do and which records matter.</li>
            <li><strong>Life-cycle approach within a QMS</strong> — controlled from concept to retirement.</li>
            <li><strong>Scalable life-cycle activities</strong> — proportionate to risk, complexity and novelty.</li>
            <li><strong>Science-based quality risk management</strong> — ICH Q9 applied to computerized systems.</li>
            <li><strong>Leveraging supplier involvement</strong> — accept fit-for-purpose supplier documentation.</li>
          </ol>
        `,
      },
    ],
  },

  {
    id: "gxp",
    title: "2. GxP, Regulated Records & Patient Safety",
    summary: "Identifying GxP impact, the records to protect, and ALCOA+.",
    sections: [
      {
        h: "What does GxP mean?",
        body: `
          <p>"GxP" is the umbrella term for Good Practice regulations covering laboratory (GLP),
          clinical (GCP), manufacturing (GMP), distribution (GDP), pharmacovigilance (GVP) and others.
          A system is "GxP" if it directly supports any of these regulated activities or holds records
          that must be retained, reviewed, reported or relied upon.</p>
        `,
      },
      {
        h: "GxP assessment questions",
        body: `
          <ul>
            <li>Does the system generate, manipulate or store data that supports a regulatory submission, batch release, clinical decision or PV report?</li>
            <li>Does it control, monitor, or record a GMP process?</li>
            <li>Could a failure of the system impact <strong>patient safety</strong>, <strong>product quality</strong> or <strong>data integrity</strong>?</li>
            <li>Are its outputs used to make a quality decision (release, reject, OOS, deviation)?</li>
          </ul>
          <p>Answer "yes" to any of these and the system is in GxP scope. Document the determination and the reasoning.</p>
        `,
      },
      {
        h: "ALCOA / ALCOA+",
        body: `
          <p>The data-integrity acronym used worldwide. GAMP 5 2nd edition expects ALCOA+ to be considered
          during <em>specification</em> and <em>design</em>, not retrofitted in operation.</p>
          <div class="table-wrap"><table class="styled">
            <thead><tr><th>Attribute</th><th>Meaning</th><th>Typical control</th></tr></thead>
            <tbody>
              <tr><td>Attributable</td><td>Traceable to the person/instrument generating the data</td><td>Unique logins, audit trails</td></tr>
              <tr><td>Legible</td><td>Readable and permanent</td><td>Backups, no overwrites</td></tr>
              <tr><td>Contemporaneous</td><td>Recorded at the time of the event</td><td>Synchronized clocks (NTP), forced commit</td></tr>
              <tr><td>Original</td><td>First capture or certified copy</td><td>Raw data retention, file-level integrity</td></tr>
              <tr><td>Accurate</td><td>Correct, complete and reflective of what occurred</td><td>Validation, calibration, review</td></tr>
              <tr><td>Complete</td><td>All data including repeats and metadata</td><td>Audit trail, full event capture</td></tr>
              <tr><td>Consistent</td><td>Same order and sequence as the activity</td><td>Workflow controls, sequenced timestamps</td></tr>
              <tr><td>Enduring</td><td>Survives the retention period</td><td>Archive, format migration plan</td></tr>
              <tr><td>Available</td><td>Retrievable throughout retention</td><td>Disaster recovery, readable media</td></tr>
            </tbody>
          </table></div>
        `,
      },
      {
        h: "Electronic records vs paper",
        body: `
          <p>Where a system is the system of record (the <em>raw data</em>), printouts are not sufficient evidence —
          the electronic record, with its audit trail and metadata, must be retained, reviewed, and protected.
          This is a frequent finding source under both 21 CFR Part 11 and Annex 11.</p>
        `,
      },
    ],
  },

  {
    id: "qrm",
    title: "3. Quality Risk Management (ICH Q9)",
    summary: "Risk-based scaling of validation activities.",
    sections: [
      {
        h: "Why risk-based?",
        body: `
          <p>Not all systems carry the same risk to patients or products. A risk-based approach focuses validation
          effort where it matters and avoids over-validation of low-risk functionality. ICH Q9(R1) provides the
          governing principles; GAMP 5 operationalizes them.</p>
        `,
      },
      {
        h: "Five-step risk approach (GAMP 5)",
        body: `
          <ol>
            <li><strong>Initial risk assessment & system impact</strong> &mdash; GxP scope, business / patient impact, novelty.</li>
            <li><strong>Identify functions with impact on patient safety, product quality, data integrity</strong> (PQDI).</li>
            <li><strong>Perform functional risk assessment</strong> &mdash; severity, probability, detectability (e.g. FMEA).</li>
            <li><strong>Identify and implement controls</strong> &mdash; design controls preferred over procedural.</li>
            <li><strong>Review risks & verify controls</strong> &mdash; through testing and ongoing monitoring.</li>
          </ol>
          <div class="callout"><strong>Hierarchy of controls:</strong>
          <em>Engineered design &gt; Configuration &gt; Procedural &gt; Detection &amp; recovery</em>.
          Always prefer to design risk out before relying on SOPs.</div>
        `,
      },
      {
        h: "Risk classification example",
        body: `
          <div class="table-wrap"><table class="styled">
            <thead><tr><th>Severity</th><th>Probability</th><th>Detectability</th><th>Risk class</th></tr></thead>
            <tbody>
              <tr><td>High</td><td>Frequent</td><td>Low</td><td><span class="badge fail">Critical</span></td></tr>
              <tr><td>High</td><td>Occasional</td><td>High</td><td><span class="badge warn">High</span></td></tr>
              <tr><td>Medium</td><td>Occasional</td><td>Medium</td><td><span class="badge warn">Medium</span></td></tr>
              <tr><td>Low</td><td>Rare</td><td>High</td><td><span class="badge ok">Low</span></td></tr>
            </tbody>
          </table></div>
          <p>Tie testing rigour and review depth to the resulting risk class.</p>
        `,
      },
    ],
  },

  {
    id: "categories",
    title: "4. Software & Hardware Categories",
    summary: "GAMP categories 1, 3, 4, 5 and how they steer validation.",
    sections: [
      {
        h: "Why classify?",
        body: `
          <p>Classification determines the <em>baseline</em> validation activities. It is not a substitute for
          risk assessment — a Category 3 system can still be high-risk because of <em>how</em> it is used.</p>
        `,
      },
      {
        h: "The four software categories",
        body: `
          <div class="card-grid">
            <div class="card">
              <span class="badge cat1">Category 1</span>
              <div class="card-title">Infrastructure software</div>
              <p class="card-body">Operating systems, databases (engine only), middleware, network management, anti-virus, programming languages, statistical packages.</p>
              <p class="card-body"><strong>Approach:</strong> Record version, qualify the platform; no functional validation of the OS itself.</p>
            </div>
            <div class="card">
              <span class="badge cat3">Category 3</span>
              <div class="card-title">Non-configured products</div>
              <p class="card-body">Commercial off-the-shelf software used "out of the box" &mdash; e.g. fixed-function instrument firmware, a standard spreadsheet without macros.</p>
              <p class="card-body"><strong>Approach:</strong> Verify intended use; supplier assessment; configuration documented only as installation parameters.</p>
            </div>
            <div class="card">
              <span class="badge cat4">Category 4</span>
              <div class="card-title">Configured products</div>
              <p class="card-body">Commercial software with significant configuration of business rules, workflows, screens, master data &mdash; e.g. LIMS, MES, EDMS, ERP, QMS.</p>
              <p class="card-body"><strong>Approach:</strong> Full life-cycle, configuration specification, supplier audit for higher risk, testing of configured functionality.</p>
            </div>
            <div class="card">
              <span class="badge cat5">Category 5</span>
              <div class="card-title">Custom applications</div>
              <p class="card-body">Bespoke code developed for the regulated user &mdash; custom apps, macros doing GxP work, custom interfaces.</p>
              <p class="card-body"><strong>Approach:</strong> Full life-cycle, design specifications, code review, structural &amp; functional testing, robust change control.</p>
            </div>
          </div>
          <div class="callout warn"><strong>Category 2 was retired</strong> in GAMP 5 1st edition (it covered firmware); firmware is now assessed as part of the device/system using the most appropriate of the remaining categories.</div>
        `,
      },
      {
        h: "Hybrid systems",
        body: `
          <p>Most real-world systems are <strong>hybrid</strong>: a Category 4 platform that contains
          Category 5 custom extensions, running on a Category 1 stack. Apply the appropriate approach to
          each component, then validate the integration.</p>
        `,
      },
    ],
  },

  {
    id: "lifecycle",
    title: "5. The System Life Cycle",
    summary: "Concept → Project → Operation → Retirement.",
    sections: [
      {
        h: "Life-cycle phases",
        body: `
          <div class="table-wrap"><table class="styled">
            <thead><tr><th>Phase</th><th>Key activities</th><th>Typical deliverables</th></tr></thead>
            <tbody>
              <tr><td>Concept</td><td>Identify need, GxP assessment, business case</td><td>Concept note, initial risk</td></tr>
              <tr><td>Project</td><td>Specify, design, build, test, release</td><td>VP, URS, FS/CS/DS, IQ/OQ/PQ, VSR</td></tr>
              <tr><td>Operation</td><td>Use the system under control</td><td>Change control, periodic review, training, backups, incident management</td></tr>
              <tr><td>Retirement</td><td>Decommission, migrate or archive data</td><td>Retirement plan, data migration validation, archive verification</td></tr>
            </tbody>
          </table></div>
        `,
      },
      {
        h: "Linear (V-model)",
        body: `
          <div class="vmodel">
            <div class="vbox vleft"><strong>URS</strong><br />User Requirements Spec</div>
            <div class="vmid">&harr;</div>
            <div class="vbox vright"><strong>PQ / UAT</strong><br />Performance Qualification</div>

            <div class="vbox vleft"><strong>FS</strong><br />Functional Spec</div>
            <div class="vmid">&harr;</div>
            <div class="vbox vright"><strong>OQ</strong><br />Operational Qualification</div>

            <div class="vbox vleft"><strong>CS / DS</strong><br />Configuration / Design Spec</div>
            <div class="vmid">&harr;</div>
            <div class="vbox vright"><strong>IQ</strong><br />Installation Qualification</div>

            <div class="vbox vleft" style="opacity:.8"><strong>Build / Configure / Code</strong></div>
            <div class="vmid">&darr;</div>
            <div class="vbox vright" style="opacity:.8"><strong>Unit / Integration testing</strong></div>
          </div>
          <p>The V-model pairs each specification with a corresponding verification activity. This is the
          traditional approach and still suits highly regulated, infrequently changing systems.</p>
        `,
      },
      {
        h: "Iterative & Agile",
        body: `
          <p>GAMP 5 2nd edition explicitly recognises iterative life cycles. The key is that the
          <strong>quality elements</strong> (specifications, verification, traceability, change control,
          formal release) still exist — they are just produced incrementally.</p>
          <ul>
            <li>Each sprint produces a small slice of specification, configuration/code and verification.</li>
            <li>Definition-of-Done includes GxP evidence: tests passed, traceability updated, review signed.</li>
            <li>A formal release/qualification gate is held before each production release.</li>
            <li>The product owner role embeds the regulated user in every decision.</li>
          </ul>
        `,
      },
    ],
  },

  {
    id: "specs",
    title: "6. Specifications & Requirements",
    summary: "URS, FS, Configuration, Design, and traceability.",
    sections: [
      {
        h: "Specification hierarchy",
        body: `
          <div class="table-wrap"><table class="styled">
            <thead><tr><th>Specification</th><th>Answers</th><th>Owner</th></tr></thead>
            <tbody>
              <tr><td>User Requirements Specification (URS)</td><td>What does the business need?</td><td>Process owner / SMEs</td></tr>
              <tr><td>Functional Specification (FS)</td><td>What will the system do?</td><td>Supplier / project</td></tr>
              <tr><td>Configuration Specification (CS)</td><td>How is the product configured?</td><td>Supplier / integrator</td></tr>
              <tr><td>Design Specification (DS) / Software Design Spec (SDS)</td><td>How is it built?</td><td>Developer</td></tr>
              <tr><td>Hardware/Network Specification</td><td>What is it installed on?</td><td>Infrastructure</td></tr>
            </tbody>
          </table></div>
        `,
      },
      {
        h: "Anatomy of a good requirement",
        body: `
          <ul>
            <li><strong>Unique ID</strong> &mdash; e.g. URS-LIMS-014.</li>
            <li><strong>Atomic</strong> &mdash; one testable statement.</li>
            <li><strong>Necessary</strong> &mdash; tied to a business or regulatory need.</li>
            <li><strong>Unambiguous</strong> &mdash; only one interpretation.</li>
            <li><strong>Verifiable</strong> &mdash; can be proven by inspection, demonstration, analysis or test.</li>
            <li><strong>Prioritised &amp; classified</strong> &mdash; GxP / non-GxP, criticality, source.</li>
          </ul>
          <div class="callout"><strong>Bad:</strong> "The system shall be fast."<br />
          <strong>Better:</strong> "URS-031: When a user queries a batch record by batch ID, the result set shall return within 5 seconds 95% of the time under normal operational load (250 concurrent users)."</div>
        `,
      },
      {
        h: "Requirements Traceability Matrix (RTM)",
        body: `
          <p>The RTM is the spine of CSV. It links every requirement to its design, configuration, test
          case and risk control. Inspectors use it to navigate the validation package.</p>
          <div class="table-wrap"><table class="styled">
            <thead><tr><th>URS</th><th>FS</th><th>Risk</th><th>Test case</th><th>Result</th></tr></thead>
            <tbody>
              <tr><td>URS-031</td><td>FS-014</td><td>R-07 (Med)</td><td>PQ-TC-012</td><td><span class="badge ok">Pass</span></td></tr>
              <tr><td>URS-032</td><td>FS-014</td><td>R-08 (High)</td><td>OQ-TC-021, PQ-TC-013</td><td><span class="badge ok">Pass</span></td></tr>
              <tr><td>URS-033</td><td>FS-015</td><td>R-09 (Low)</td><td>OQ-TC-022</td><td><span class="badge ok">Pass</span></td></tr>
            </tbody>
          </table></div>
        `,
      },
    ],
  },

  {
    id: "testing",
    title: "7. Verification, IQ / OQ / PQ & UAT",
    summary: "Testing strategy, qualification stages and good evidence.",
    sections: [
      {
        h: "Verification vs validation",
        body: `
          <p><strong>Verification</strong> asks: "Did we build the system right?" — it confirms the system
          meets its specifications. <strong>Validation</strong> asks: "Did we build the right system?" —
          it confirms the system meets the intended use in the target process. Validation needs verification
          plus user acceptance / PQ in the live environment.</p>
        `,
      },
      {
        h: "Qualification stages",
        body: `
          <div class="table-wrap"><table class="styled">
            <thead><tr><th>Stage</th><th>Confirms</th><th>Typical evidence</th></tr></thead>
            <tbody>
              <tr><td>DQ &mdash; Design Qualification</td><td>The design meets the URS</td><td>Reviewed FS/DS/CS, traceability</td></tr>
              <tr><td>IQ &mdash; Installation Qualification</td><td>The system is installed correctly</td><td>Hardware checklists, version checks, infrastructure qualification</td></tr>
              <tr><td>OQ &mdash; Operational Qualification</td><td>Functions work as specified</td><td>Functional test scripts, boundary &amp; negative tests</td></tr>
              <tr><td>PQ &mdash; Performance Qualification</td><td>The system performs in the real process</td><td>End-to-end business scenarios, UAT, load tests, data integrity tests</td></tr>
            </tbody>
          </table></div>
        `,
      },
      {
        h: "Test script anatomy",
        body: `
          <ul>
            <li>Unique ID, version, author, reviewer, approver.</li>
            <li>Reference to URS/FS being verified.</li>
            <li>Prerequisites (data, accounts, roles, environment).</li>
            <li>Numbered steps with expected result for each.</li>
            <li>Recorded actual result, attached evidence (screenshots, exports, audit trail).</li>
            <li>Pass / fail / NA — with rationale where NA.</li>
            <li>Deviation log with disposition.</li>
          </ul>
        `,
      },
      {
        h: "Good evidence",
        body: `
          <ul>
            <li>Capture the screen <em>and</em> the audit trail entry for the action.</li>
            <li>Show the user who performed the step (logged-in user, timestamp).</li>
            <li>Retain raw test data (input files, exports) — not just a "pass" tick.</li>
            <li>Annotated screenshots beat free-text descriptions.</li>
          </ul>
          <div class="callout warn"><strong>Common finding:</strong> evidence shows the expected result but doesn't show <em>who</em> performed it or <em>when</em>. Always capture the audit trail.</div>
        `,
      },
      {
        h: "Risk-based test scaling",
        body: `
          <p>For Category 3 / low-risk functions, focused intended-use testing may be sufficient. For
          Category 5 / high-risk functions, expect boundary tests, negative tests, stress tests, security
          tests and full traceability.</p>
        `,
      },
    ],
  },

  {
    id: "suppliers",
    title: "8. Suppliers, Supplier Assessment & Leveraging",
    summary: "How to assess vendors and reuse their evidence.",
    sections: [
      {
        h: "Supplier assessment",
        body: `
          <p>Before placing a regulated system with a supplier, assess their quality system. The depth of
          assessment is risk-based: from a documented questionnaire (low risk) to an on-site audit
          (high risk, Category 4/5).</p>
          <ul>
            <li>QMS &amp; ISO 9001 / ISO 27001 certifications.</li>
            <li>SDLC, change &amp; release management, defect tracking.</li>
            <li>Test strategy, regression, vulnerability management.</li>
            <li>Cloud: tenancy model, sub-processors, SOC 2 Type II, ISO 27017/27018.</li>
            <li>Compliance posture vs Part 11 / Annex 11, willingness to share evidence.</li>
          </ul>
        `,
      },
      {
        h: "Leveraging supplier documentation",
        body: `
          <p>GAMP 5 explicitly encourages reusing fit-for-purpose supplier work. To leverage you must:</p>
          <ol>
            <li>Confirm the supplier QMS through assessment / audit.</li>
            <li>Map the supplier's deliverables to your life-cycle deliverables.</li>
            <li>Verify the documents are current, controlled, and traceable to the delivered version.</li>
            <li>Identify and address gaps with regulated-user activities.</li>
          </ol>
          <div class="callout ok"><strong>Lean tip:</strong> a credible supplier IQ + a regulated-user
          installation verification often replaces a full bespoke IQ.</div>
        `,
      },
      {
        h: "Quality Agreement",
        body: `
          <p>For ongoing services (SaaS, managed hosting), a Quality / Technical Agreement formalises
          responsibilities: change notification, incident handling, backups, audit trails, data return
          on exit, audit rights.</p>
        `,
      },
    ],
  },

  {
    id: "change",
    title: "9. Change & Configuration Management",
    summary: "Keeping the system in a validated state.",
    sections: [
      {
        h: "Change control essentials",
        body: `
          <p>A documented change control process must:</p>
          <ul>
            <li>Classify the change (GxP / non-GxP, emergency / planned, like-for-like / functional).</li>
            <li>Perform a risk and regression impact assessment.</li>
            <li>Define the qualification scope (no test, partial test, full re-qualification).</li>
            <li>Approve before implementation, verify after, and update affected documents.</li>
          </ul>
        `,
      },
      {
        h: "Configuration management",
        body: `
          <ul>
            <li>Maintain a Configuration Item (CI) list: software versions, infrastructure, interfaces, master data.</li>
            <li>Baseline the configuration after release; capture the baseline in the IQ.</li>
            <li>Tie configuration changes to the change control record and the RTM.</li>
          </ul>
        `,
      },
      {
        h: "Regression strategy",
        body: `
          <p>Maintain a <strong>regression test pack</strong> that covers the GxP-critical functions and
          interfaces. Run it for every release. Automation pays off here — it is one of the few places
          where validated automated tests provide a strong ROI.</p>
        `,
      },
    ],
  },

  {
    id: "operation",
    title: "10. Operational Phase",
    summary: "Live-running activities to maintain compliance.",
    sections: [
      {
        h: "Operational processes",
        body: `
          <div class="card-grid">
            <div class="card"><div class="card-title">Access management</div><p class="card-body">Joiner/mover/leaver process, least privilege, periodic access review.</p></div>
            <div class="card"><div class="card-title">Backup & restore</div><p class="card-body">Documented backup, periodic restore test, retention &amp; storage.</p></div>
            <div class="card"><div class="card-title">Business continuity / DR</div><p class="card-body">BCP/DR plans, RTO/RPO, periodic exercise.</p></div>
            <div class="card"><div class="card-title">Incident & problem mgmt</div><p class="card-body">Incident logging, root cause, CAPA where appropriate.</p></div>
            <div class="card"><div class="card-title">Periodic review</div><p class="card-body">Risk-based cadence, evaluates state of control.</p></div>
            <div class="card"><div class="card-title">Training</div><p class="card-body">Role-based, qualified before access, recurrent.</p></div>
            <div class="card"><div class="card-title">Audit trail review</div><p class="card-body">Risk-based review of audit trails for critical events.</p></div>
            <div class="card"><div class="card-title">Data archiving</div><p class="card-body">Readable across retention; media migration plan.</p></div>
          </div>
        `,
      },
      {
        h: "Periodic review",
        body: `
          <p>Every GxP system needs periodic evaluation that the system remains in a validated state.
          Typical inputs:</p>
          <ul>
            <li>Change history since last review and its impact.</li>
            <li>Deviations, incidents, CAPAs, problem trends.</li>
            <li>Open patches, end-of-life components, security findings.</li>
            <li>User access review &amp; SoD violations.</li>
            <li>Audit trail review evidence.</li>
            <li>SOP currency &amp; training compliance.</li>
            <li>DR/BCP exercise results &amp; backup restore tests.</li>
          </ul>
          <p>Output: a signed periodic review report with actions tracked through QMS.</p>
        `,
      },
      {
        h: "Audit trail review",
        body: `
          <p>Annex 11 §9 and Part 11 §11.10(e) require that audit trails be reviewed. Define what is
          critical, the review cadence and the evidence captured. Use exception-based queries — reviewing
          every line is impractical for busy systems.</p>
        `,
      },
    ],
  },

  {
    id: "retirement",
    title: "11. Retirement, Migration & Archiving",
    summary: "Ending the life cycle without losing data integrity.",
    sections: [
      {
        h: "Retirement plan",
        body: `
          <ul>
            <li>Inventory of records, their retention period and legal/regulatory holds.</li>
            <li>Decision: archive in place, migrate to a successor, export to a read-only archive.</li>
            <li>If migrating: validate the migration (extract, transform, load, reconciliation).</li>
            <li>Decommission steps: license return, secure data destruction where appropriate, infrastructure decommissioning.</li>
            <li>Final retirement report and update of system inventory.</li>
          </ul>
        `,
      },
      {
        h: "Data migration validation",
        body: `
          <p>Migrating GxP data is itself a GxP activity:</p>
          <ol>
            <li>Migration plan with mapping spec and reconciliation strategy.</li>
            <li>Pilot migration in a controlled environment.</li>
            <li>Reconciliation: record counts, checksums, sample-based content verification, audit trail handling.</li>
            <li>Cutover &amp; post-cutover verification.</li>
            <li>Migration report and update of the receiving system's validation package.</li>
          </ol>
        `,
      },
    ],
  },

  {
    id: "critical-thinking",
    title: "12. Critical Thinking",
    summary: "A new core theme in GAMP 5 2nd edition.",
    sections: [
      {
        h: "What is critical thinking in CSV?",
        body: `
          <p>Critical thinking is the disciplined application of knowledge, experience and risk to decide
          <em>what</em> to do — rather than mechanically running a template. GAMP 5 2nd edition makes it an
          explicit, central concept.</p>
        `,
      },
      {
        h: "Where to apply it",
        body: `
          <ul>
            <li>Tailoring deliverables to system risk and complexity (not "one VP fits all").</li>
            <li>Deciding which supplier evidence to leverage and which gaps to plug.</li>
            <li>Defining test depth and selecting boundary, negative and edge cases.</li>
            <li>Deciding when to retire test cases that no longer add value.</li>
            <li>Spotting findings that look procedural but are really design problems.</li>
          </ul>
          <div class="callout"><strong>Anti-pattern:</strong> running the same 200-step IQ on a cloud SaaS that you do
          on an on-prem server. The risk profile is different; the script should be too.</div>
        `,
      },
    ],
  },

  {
    id: "agile",
    title: "13. Agile & Iterative Development",
    summary: "Sprints, increments and GxP evidence.",
    sections: [
      {
        h: "Agile is compatible with CSV",
        body: `
          <p>Agile fulfils CSV expectations if quality activities are integrated into each iteration. The
          documents and evidence required by Annex 11 / Part 11 are produced incrementally, not skipped.</p>
        `,
      },
      {
        h: "Mapping Agile to CSV deliverables",
        body: `
          <div class="table-wrap"><table class="styled">
            <thead><tr><th>Agile artefact</th><th>CSV role</th></tr></thead>
            <tbody>
              <tr><td>Product backlog (user stories with acceptance criteria)</td><td>URS, version-controlled</td></tr>
              <tr><td>Sprint backlog &amp; design notes</td><td>FS/CS/DS slice for the sprint</td></tr>
              <tr><td>Automated unit, integration, regression tests</td><td>Verification evidence (qualified test framework)</td></tr>
              <tr><td>Definition of Done</td><td>Quality gate including traceability, code review, evidence capture</td></tr>
              <tr><td>Release qualification</td><td>Formal IQ/OQ/PQ before production release</td></tr>
              <tr><td>Sprint review / retrospective</td><td>Continuous improvement, input to periodic review</td></tr>
            </tbody>
          </table></div>
        `,
      },
      {
        h: "Pitfalls",
        body: `
          <ul>
            <li>"Working software over documentation" reinterpreted as "no documentation" — non-compliant.</li>
            <li>No formal release gate before production — non-compliant.</li>
            <li>Automated tests not themselves qualified / version-controlled — weak evidence.</li>
            <li>Stories without testable acceptance criteria — no verification baseline.</li>
          </ul>
        `,
      },
    ],
  },

  {
    id: "infra-services",
    title: "14. IT Infrastructure & Services",
    summary: "Qualified platforms, ITIL alignment, leveraging shared services.",
    sections: [
      {
        h: "Infrastructure as a qualified platform",
        body: `
          <p>Infrastructure (compute, storage, network, OS, database engines, virtualisation) is
          Category 1 and is <em>qualified</em>, not validated. Once qualified and operated under controlled
          processes, applications can be deployed onto it without re-qualifying the platform.</p>
        `,
      },
      {
        h: "ITIL processes that matter",
        body: `
          <ul>
            <li>Change &amp; release management</li>
            <li>Incident &amp; problem management</li>
            <li>Configuration management (CMDB)</li>
            <li>Capacity &amp; availability management</li>
            <li>Information security management</li>
            <li>Supplier &amp; service-level management</li>
          </ul>
          <p>These processes <em>are</em> CSV controls — they provide the ongoing state of control.</p>
        `,
      },
    ],
  },

  {
    id: "cloud",
    title: "15. Cloud, SaaS & Multi-tenant Systems",
    summary: "Validating systems you don't fully control.",
    sections: [
      {
        h: "Shared responsibility",
        body: `
          <div class="table-wrap"><table class="styled">
            <thead><tr><th>Responsibility</th><th>IaaS</th><th>PaaS</th><th>SaaS</th></tr></thead>
            <tbody>
              <tr><td>Physical &amp; network security</td><td>Provider</td><td>Provider</td><td>Provider</td></tr>
              <tr><td>OS / middleware / DB</td><td>Customer</td><td>Provider</td><td>Provider</td></tr>
              <tr><td>Application configuration</td><td>Customer</td><td>Customer</td><td>Customer</td></tr>
              <tr><td>Data, user access, audit trail review</td><td>Customer</td><td>Customer</td><td>Customer</td></tr>
              <tr><td>GxP validation evidence</td><td>Customer (with leveraging)</td><td>Customer (with leveraging)</td><td>Customer (with leveraging)</td></tr>
            </tbody>
          </table></div>
        `,
      },
      {
        h: "Key cloud risks for CSV",
        body: `
          <ul>
            <li><strong>Continuous updates:</strong> the supplier may push changes without your approval.
            Establish change notification &amp; veto windows in the Quality Agreement.</li>
            <li><strong>Multi-tenancy:</strong> ensure logical isolation, tenant-specific audit trails, configuration boundaries.</li>
            <li><strong>Sub-processors:</strong> assess the supply chain, not just the front door.</li>
            <li><strong>Data location &amp; sovereignty:</strong> particularly for clinical and PV data.</li>
            <li><strong>Exit strategy:</strong> data export format, retention obligations after termination.</li>
          </ul>
        `,
      },
      {
        h: "Cloud validation strategy",
        body: `
          <ol>
            <li>Strong supplier assessment (audit + SOC 2 Type II + ISO 27001/27017/27018 + Part 11 / Annex 11 statement).</li>
            <li>Document leveraging plan; do not duplicate the supplier's IQ for shared platform components.</li>
            <li>Focus your validation on configuration, integrations, identity &amp; access, audit trail and business process testing.</li>
            <li>Operational controls for continuous releases: notification windows, sandbox/test tenant, regression suite.</li>
          </ol>
        `,
      },
    ],
  },

  {
    id: "aiml",
    title: "16. AI / Machine Learning Systems",
    summary: "Validating systems that learn or adapt.",
    sections: [
      {
        h: "Why AI/ML is different",
        body: `
          <p>Traditional software is <em>deterministic</em>: same input, same output. ML models are
          <em>probabilistic</em> and may <em>change over time</em> as they are retrained. GAMP 5 2nd
          edition introduces a risk approach without overturning the life cycle.</p>
        `,
      },
      {
        h: "Lifecycle additions for ML",
        body: `
          <ul>
            <li><strong>Data lifecycle:</strong> training, validation and test datasets are themselves controlled GxP records (provenance, version, bias assessment).</li>
            <li><strong>Model lifecycle:</strong> versioned, with performance metrics defined and acceptance thresholds.</li>
            <li><strong>Locked vs adaptive models:</strong> for GxP, prefer locked models with controlled retraining and re-qualification.</li>
            <li><strong>Explainability &amp; human oversight:</strong> proportionate to risk — required for safety-critical decisions.</li>
            <li><strong>Monitoring:</strong> drift detection, performance trending, retraining triggers.</li>
          </ul>
          <div class="callout warn"><strong>Continuously learning models</strong> in production for GxP decisions
          remain very challenging. Most regulated deployments today use periodically retrained, locked models with controlled change control.</div>
        `,
      },
    ],
  },

  {
    id: "data-integrity",
    title: "17. Data Integrity by Design",
    summary: "Building ALCOA+ into the system rather than auditing it in.",
    sections: [
      {
        h: "Design-time controls",
        body: `
          <ul>
            <li>Unique user accounts, password complexity, MFA where risk warrants.</li>
            <li>Role-based access &amp; segregation of duties (especially review/approve).</li>
            <li>Forced audit trail on GxP data — cannot be disabled by users.</li>
            <li>System time synchronised to a trusted source; no user time override.</li>
            <li>Controlled date / number formatting, locale, decimal handling.</li>
            <li>Disabled or controlled use of generic / shared accounts (and never for GxP signing).</li>
            <li>Controlled file system access — raw data not user-accessible to delete or rename.</li>
            <li>Backup before any data manipulation.</li>
          </ul>
        `,
      },
      {
        h: "Operational controls",
        body: `
          <ul>
            <li>Periodic access reviews &amp; audit trail review.</li>
            <li>Test-environment data segregated from production.</li>
            <li>Change control over reference / master data.</li>
            <li>Data lifecycle: capture, processing, reporting, archival, destruction — all under SOPs.</li>
            <li>Training on data integrity expectations, including hybrid (paper + electronic) workflows.</li>
          </ul>
        `,
      },
    ],
  },

  {
    id: "esig",
    title: "18. Electronic Signatures",
    summary: "Identity, intent and binding — done electronically.",
    sections: [
      {
        h: "Requirements common to Part 11 and Annex 11",
        body: `
          <ul>
            <li><strong>Uniqueness</strong> — each signature traceable to one individual.</li>
            <li><strong>Identity verification</strong> before issuing a signing credential.</li>
            <li><strong>At least two distinct components</strong> for non-biometric signatures (e.g. ID + password).</li>
            <li><strong>Re-authentication</strong> for the first signing in a session and for high-risk signings.</li>
            <li><strong>Meaning of signature</strong> displayed (review, approval, authorship, responsibility).</li>
            <li><strong>Indelible link</strong> between the signature and the signed record.</li>
            <li><strong>Audit trail</strong> recording who signed, when, and the action being signed.</li>
            <li><strong>Equivalent to handwritten</strong> when designed and operated correctly.</li>
          </ul>
        `,
      },
      {
        h: "Special considerations",
        body: `
          <ul>
            <li>Biometric signatures must be designed to be only usable by their genuine owner.</li>
            <li>Anti-repudiation: controls so individuals cannot deny their signature later.</li>
            <li>Cross-border use of e-signatures: align with local civil law (eIDAS in the EU, ESIGN/UETA in the US).</li>
          </ul>
        `,
      },
    ],
  },

  {
    id: "deliverables",
    title: "19. Typical CSV Deliverables Pack",
    summary: "A right-sized list of documents for a new system.",
    sections: [
      {
        h: "Documentation set",
        body: `
          <div class="table-wrap"><table class="styled">
            <thead><tr><th>Document</th><th>Purpose</th><th>Risk-scaled?</th></tr></thead>
            <tbody>
              <tr><td>Validation Plan (VP)</td><td>Strategy, roles, deliverables, scope, risk approach</td><td>Yes</td></tr>
              <tr><td>GxP Assessment</td><td>Confirms regulatory scope</td><td>No</td></tr>
              <tr><td>System Risk Assessment</td><td>Initial &amp; functional risk</td><td>Yes</td></tr>
              <tr><td>URS</td><td>What the business needs</td><td>Yes</td></tr>
              <tr><td>Supplier Assessment</td><td>Vendor capability &amp; leveraging plan</td><td>Yes</td></tr>
              <tr><td>FS / CS / DS</td><td>What/how the system will do</td><td>Yes</td></tr>
              <tr><td>Configuration Spec / Build records</td><td>How the system is configured</td><td>Yes</td></tr>
              <tr><td>Test Plan, IQ, OQ, PQ scripts &amp; reports</td><td>Verification evidence</td><td>Yes</td></tr>
              <tr><td>Traceability Matrix</td><td>End-to-end coverage proof</td><td>Yes</td></tr>
              <tr><td>Data migration plan &amp; report</td><td>If applicable</td><td>Yes</td></tr>
              <tr><td>Training records</td><td>Users qualified before access</td><td>No</td></tr>
              <tr><td>SOPs (admin, user, backup, DR, change, periodic review)</td><td>Operate the validated state</td><td>No</td></tr>
              <tr><td>Validation Summary Report (VSR)</td><td>Conclusion &amp; release for use</td><td>Yes</td></tr>
              <tr><td>Periodic review &amp; retirement plan</td><td>Ongoing &amp; end-of-life</td><td>Yes</td></tr>
            </tbody>
          </table></div>
        `,
      },
    ],
  },

  {
    id: "inspections",
    title: "20. Inspections & Common Findings",
    summary: "How regulators probe CSV — and where most companies stumble.",
    sections: [
      {
        h: "What inspectors look at",
        body: `
          <ul>
            <li>System inventory &amp; GxP classification.</li>
            <li>Validation Summary Report and the RTM behind it.</li>
            <li>A sampled traceability "thread" from a URS line to a passed test step.</li>
            <li>Change records since release &mdash; was the validation state maintained?</li>
            <li>Audit trail review evidence for a chosen period.</li>
            <li>User access list, leavers, SoD.</li>
            <li>Backup &amp; restore test records.</li>
            <li>Periodic review reports.</li>
            <li>Supplier audit / leveraging evidence.</li>
            <li>Data integrity controls — both technical and behavioural.</li>
          </ul>
        `,
      },
      {
        h: "Recurring findings",
        body: `
          <div class="table-wrap"><table class="styled">
            <thead><tr><th>Finding</th><th>Root cause</th><th>Prevention</th></tr></thead>
            <tbody>
              <tr><td>Shared / generic accounts in use</td><td>Convenience over control</td><td>Design out at SSO/IAM layer</td></tr>
              <tr><td>Audit trail not reviewed</td><td>Volume too high, no procedure</td><td>Risk-based exception queries</td></tr>
              <tr><td>Spreadsheets used as records without validation</td><td>Tool not classified</td><td>Inventory all GxP tools incl. spreadsheets</td></tr>
              <tr><td>Test evidence missing user/timestamp</td><td>Screenshots cropped</td><td>Train and review evidence quality</td></tr>
              <tr><td>Configuration drift from validated baseline</td><td>Unmanaged changes</td><td>CMDB + change control</td></tr>
              <tr><td>"Validated supplier" claim with no evidence</td><td>No supplier assessment</td><td>Documented assessment &amp; leveraging plan</td></tr>
              <tr><td>Date format ambiguous (MM/DD vs DD/MM)</td><td>Locale not controlled</td><td>Force locale at system level</td></tr>
              <tr><td>Backups never restored</td><td>Confidence ≠ evidence</td><td>Periodic restore exercise</td></tr>
            </tbody>
          </table></div>
        `,
      },
    ],
  },
];
