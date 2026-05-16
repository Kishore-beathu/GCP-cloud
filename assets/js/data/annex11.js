/* EU GMP Annex 11 — Computerised Systems (EudraLex Volume 4).
   Educational paraphrase. Always consult the official text for binding interpretation. */

window.ANNEX11 = {
  title: "EU GMP Annex 11 — Computerised Systems",
  intro: `
    <p>Annex 11 sits within EudraLex Volume 4 (EU GMP) and applies to <strong>all forms of
    computerised systems used as part of a GMP regulated activity</strong>. It applies in parallel
    with Chapter 4 (Documentation). The annex is organised into a general principle, four
    "general" sections covering the project phase, and operational sections covering the live
    phase.</p>
    <p>The current revised draft (released 2025 for consultation) modernises the text — see the
    EMA / EC for the binding version applicable on your date.</p>
  `,
  sections: [
    {
      ref: "Principle",
      title: "Principle",
      summary: "Replacement of manual systems by computerised ones must not result in lower product quality, process control or quality assurance.",
      body: `
        <ul>
          <li>The risk of a computerised system must not be greater than the manual process it replaces.</li>
          <li>Particular attention should be paid to records, audit trails, electronic signatures and the validation effort proportionate to risk.</li>
        </ul>
      `,
    },
    {
      ref: "1",
      title: "Risk Management",
      summary: "Risk management applied throughout the life cycle, balancing patient safety, data integrity and product quality.",
      body: `
        <p>Decisions on the extent of validation, controls and ongoing monitoring should be based on a
        justified and documented risk assessment. ICH Q9 is the reference standard.</p>
      `,
    },
    {
      ref: "2",
      title: "Personnel",
      summary: "Close cooperation between process owners, system owners, qualified persons and IT.",
      body: `
        <p>Defined responsibilities and appropriate qualifications/training for all staff including
        administrators, developers, users and Qualified Persons. Roles must be documented.</p>
      `,
    },
    {
      ref: "3",
      title: "Suppliers and Service Providers",
      summary: "Formal agreements with suppliers; competence assessment; audit-based reliance.",
      body: `
        <p>When third parties (e.g. suppliers, service providers) are used, formal agreements must
        define their responsibilities. Competence and reliability are key criteria; assessment of
        suppliers should be based on risk. IT departments should be considered analogous to suppliers.</p>
      `,
    },
    {
      ref: "4",
      title: "Validation",
      summary: "Validation documentation, inventory, lifecycle, traceability, evidence and supplier leveraging.",
      body: `
        <ul>
          <li>Validation documentation and reports should cover the relevant steps of the life cycle.</li>
          <li>Manufacturers should be able to justify their standards, protocols, acceptance criteria, procedures and records.</li>
          <li>An up-to-date <strong>inventory of all relevant systems</strong> and their GxP functionality should be maintained.</li>
          <li>For critical systems, current detailed description with physical and logical arrangements, data flows and interfaces with other systems / processes.</li>
          <li>URS should describe the required functions; risk-based traceability to URS through the life cycle.</li>
          <li>The regulated user takes all reasonable steps to ensure the system has been developed in a quality management system; supplier assessment performed.</li>
          <li>Evidence of appropriate test methods and scenarios — in particular, system parameter limits, data limits and error handling.</li>
          <li>For migrated data — verify integrity and reliability of data migration.</li>
        </ul>
      `,
    },
    {
      ref: "5",
      title: "Data",
      summary: "Integrity of data exchanged electronically must be checked.",
      body: `
        <p>Computerised systems exchanging data electronically with other systems should include
        appropriate built-in checks for the correct and secure entry and processing of the data, in
        order to minimise risks (e.g. handshakes, checksums, validation of input).</p>
      `,
    },
    {
      ref: "6",
      title: "Accuracy Checks",
      summary: "Manually entered critical data should be checked.",
      body: `
        <p>For critical data entered manually, there should be an additional check on the accuracy of
        the data. This check may be done by a second operator or by validated electronic means.
        The criticality and the consequences of erroneous data drive the extent of this control.</p>
      `,
    },
    {
      ref: "7",
      title: "Data Storage",
      summary: "Protection against damage and routine backup with periodic restore testing.",
      body: `
        <ul>
          <li>Data should be secured by both physical and electronic means against damage. Stored
          data should be checked for accessibility, readability and accuracy.</li>
          <li>Backups should be made at periodic intervals. The integrity and accuracy of backup data
          and the ability to restore data should be checked during validation and monitored
          periodically.</li>
        </ul>
      `,
    },
    {
      ref: "8",
      title: "Printouts",
      summary: "Clear printed copies; indication of data changes for batch records.",
      body: `
        <ul>
          <li>Clear printed copies of electronically stored data should be available.</li>
          <li>For records supporting batch release, it must be possible to generate printouts indicating
          if any of the data has been changed since the original entry.</li>
        </ul>
      `,
    },
    {
      ref: "9",
      title: "Audit Trails",
      summary: "Record GMP-relevant changes and deletions; reviewed regularly.",
      body: `
        <p>Consideration should be given, based on a risk assessment, to building into the system the
        creation of a record of all GMP-relevant changes and deletions (a system-generated
        <em>"audit trail"</em>). For change or deletion of GMP-relevant data the reason should be
        documented. Audit trails should be available, convertible to a generally intelligible form
        and regularly reviewed.</p>
      `,
    },
    {
      ref: "10",
      title: "Change and Configuration Management",
      summary: "Formal change control records; validated state maintained.",
      body: `
        <p>Any changes to a computerised system including system configurations should only be made
        in a controlled manner in accordance with a defined procedure.</p>
      `,
    },
    {
      ref: "11",
      title: "Periodic Evaluation",
      summary: "Periodic review of continued validation status.",
      body: `
        <p>Computerised systems should be periodically evaluated to confirm that they remain in a
        valid state and are compliant with GMP. Such evaluations should include, where appropriate,
        the current range of functionality, deviation records, incidents, problems, upgrade history,
        performance, reliability, security and validation status reports.</p>
      `,
    },
    {
      ref: "12",
      title: "Security",
      summary: "Physical / logical controls; only authorised persons; reference to identity management.",
      body: `
        <ul>
          <li>Physical and/or logical controls should be in place to restrict access to computerised systems to authorised persons.</li>
          <li>Methods to enforce security may include the use of keys, pass cards, personal codes with passwords, biometrics, restricted access to computer equipment and data storage areas.</li>
          <li>The extent of security controls depends on the criticality of the system.</li>
          <li>Creation, change, and cancellation of access authorisations should be recorded.</li>
          <li>Management systems for data and for documents should be designed to record the identity of operators entering, changing, confirming or deleting data including date and time.</li>
        </ul>
      `,
    },
    {
      ref: "13",
      title: "Incident Management",
      summary: "All incidents reported and assessed; root cause; CAPA.",
      body: `
        <p>All incidents, not only system failures and data errors, should be reported and assessed.
        The root cause of a critical incident should be identified and should form the basis of
        corrective and preventive actions.</p>
      `,
    },
    {
      ref: "14",
      title: "Electronic Signature",
      summary: "Electronic records may be signed electronically with the same impact as handwritten signatures.",
      body: `
        <ul>
          <li>Have the same impact as hand-written signatures within the boundaries of the company.</li>
          <li>Be permanently linked to their respective record.</li>
          <li>Include the time and date that they were applied.</li>
        </ul>
      `,
    },
    {
      ref: "15",
      title: "Batch Release",
      summary: "Certification by QP supported by an electronic system using e-signatures.",
      body: `
        <p>When a computerised system is used for recording certification and batch release, the
        system should allow only Qualified Persons to certify the release of the batches and it
        should clearly identify and record the person releasing or certifying the batches. This
        should be performed using an electronic signature.</p>
      `,
    },
    {
      ref: "16",
      title: "Business Continuity",
      summary: "Plans to ensure availability of critical systems.",
      body: `
        <p>For the availability of computerised systems supporting critical processes, provisions
        should be made to ensure continuity of support for those processes in the event of a system
        breakdown (e.g. a manual or alternative system). The time required to bring the alternative
        arrangements into use should be based on risk and appropriate for a particular system and the
        business process it supports. These arrangements should be adequately documented and tested.</p>
      `,
    },
    {
      ref: "17",
      title: "Archiving",
      summary: "Data may be archived; check accessibility, readability and integrity.",
      body: `
        <p>Data may be archived. This data should be checked for accessibility, readability and
        integrity. If relevant changes are to be made to the system (e.g. computer equipment or
        programs), then the ability to retrieve the data should be ensured and tested.</p>
      `,
    },
  ],
};
