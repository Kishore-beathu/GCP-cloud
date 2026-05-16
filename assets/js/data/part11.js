/* 21 CFR Part 11 — Electronic Records; Electronic Signatures.
   Paraphrased educational reference. Always consult the official US FDA text. */

window.PART11 = {
  title: "21 CFR Part 11 — Electronic Records; Electronic Signatures",
  intro: `
    <p>Part 11 establishes the FDA's criteria under which electronic records and electronic
    signatures are considered <strong>trustworthy, reliable, and generally equivalent to paper
    records and handwritten signatures</strong>. It applies whenever an electronic record is
    created, modified, maintained, archived, retrieved or transmitted under any FDA-predicate
    regulation (e.g. 21 CFR 211 for drugs, 820 for medical devices, 58 for GLP, 312 for IND).</p>
    <p>The FDA's 2003 Scope and Application guidance introduced <em>enforcement discretion</em>
    for certain elements, narrowing day-to-day enforcement to records that are <em>required</em>
    by a predicate rule and that are kept in electronic format. This does not remove the
    underlying obligations — particularly around audit trails, validation and copies of records.</p>
  `,
  subparts: [
    {
      title: "Subpart A — General Provisions",
      sections: [
        {
          ref: "§11.1",
          title: "Scope",
          body: `
            <p>Part 11 applies to records in electronic form that are created, modified, maintained,
            archived, retrieved, or transmitted under any records requirements set forth in agency
            regulations, as well as electronic records submitted to the agency.</p>
            <p>Electronic signatures executed to electronic records are considered the equivalent of
            handwritten signatures when in compliance with the regulation.</p>
          `,
        },
        {
          ref: "§11.2",
          title: "Implementation",
          body: `
            <p>For records required to be maintained but not submitted to the agency, persons may
            use electronic records in lieu of paper records, provided the requirements of Part 11
            are met. For submissions, persons may use electronic records in lieu of paper records
            when the agency has accepted the submission type in electronic form.</p>
          `,
        },
        {
          ref: "§11.3",
          title: "Definitions",
          body: `
            <ul>
              <li><strong>Biometrics</strong> &mdash; a method of verifying identity based on measurement of an individual's physical or behavioural characteristics.</li>
              <li><strong>Closed system</strong> &mdash; an environment in which system access is controlled by persons who are responsible for the content of the electronic records on the system.</li>
              <li><strong>Digital signature</strong> &mdash; an electronic signature based upon cryptographic methods of originator authentication.</li>
              <li><strong>Electronic record</strong> &mdash; any combination of text, graphics, data, audio, pictorial or other information representation in digital form that is created, modified, maintained, archived, retrieved or distributed by a computer system.</li>
              <li><strong>Electronic signature</strong> &mdash; a compilation of any symbol or series of symbols executed, adopted or authorized by an individual to be the legally binding equivalent of the individual's handwritten signature.</li>
              <li><strong>Open system</strong> &mdash; an environment in which system access is not controlled by persons responsible for the content of the electronic records on the system.</li>
              <li><strong>Handwritten signature</strong> &mdash; the scripted name or legal mark of an individual handwritten by that individual and executed or adopted with the present intention to authenticate a writing in a permanent form.</li>
            </ul>
          `,
        },
      ],
    },
    {
      title: "Subpart B — Electronic Records",
      sections: [
        {
          ref: "§11.10",
          title: "Controls for Closed Systems",
          body: `
            <p>Procedures and controls designed to ensure authenticity, integrity, and confidentiality
            of records, including:</p>
            <ol type="a">
              <li><strong>Validation</strong> of systems to ensure accuracy, reliability, consistent intended performance, and the ability to discern invalid or altered records.</li>
              <li>Ability to generate <strong>accurate and complete copies</strong> of records in both human readable and electronic form suitable for inspection, review and copying by the agency.</li>
              <li><strong>Protection of records</strong> to enable their accurate and ready retrieval throughout the records retention period.</li>
              <li><strong>Limiting system access</strong> to authorized individuals.</li>
              <li>Use of <strong>secure, computer-generated, time-stamped audit trails</strong> to independently record the date and time of operator entries and actions that create, modify or delete electronic records. Record changes shall not obscure previously recorded information. Audit trail documentation shall be retained for a period at least as long as that required for the subject records and shall be available for agency review and copying.</li>
              <li>Use of <strong>operational system checks</strong> to enforce permitted sequencing of steps and events, as appropriate.</li>
              <li>Use of <strong>authority checks</strong> to ensure that only authorized individuals can use the system, electronically sign a record, access the operation or computer system input or output device, alter a record, or perform the operation at hand.</li>
              <li>Use of <strong>device checks</strong> to determine, as appropriate, the validity of the source of data input or operational instruction.</li>
              <li>Determination that persons who develop, maintain, or use electronic record/signature systems have the <strong>education, training, and experience</strong> to perform their assigned tasks.</li>
              <li>Establishment of, and adherence to, <strong>written policies</strong> that hold individuals accountable and responsible for actions initiated under their electronic signatures.</li>
              <li>Use of appropriate controls over <strong>systems documentation</strong> including access, distribution and revision/change control of documentation.</li>
            </ol>
          `,
        },
        {
          ref: "§11.30",
          title: "Controls for Open Systems",
          body: `
            <p>Open systems used to create, modify, maintain, or transmit electronic records must
            employ procedures and controls designed to ensure the authenticity, integrity, and, as
            appropriate, the confidentiality of electronic records from the point of their creation
            to the point of their receipt. Such procedures and controls shall include those
            identified in §11.10, as appropriate, and additional measures such as <strong>document
            encryption</strong> and use of appropriate <strong>digital signature</strong> standards.</p>
          `,
        },
        {
          ref: "§11.50",
          title: "Signature Manifestations",
          body: `
            <p>Signed electronic records must contain information associated with the signing that
            clearly indicates:</p>
            <ul>
              <li>The printed name of the signer.</li>
              <li>The date and time when the signature was executed.</li>
              <li>The meaning (such as review, approval, responsibility, or authorship) associated with the signature.</li>
            </ul>
            <p>The signature information shall be subject to the same controls as for electronic records and shall be included as part of any human readable form of the electronic record (such as electronic display or printout).</p>
          `,
        },
        {
          ref: "§11.70",
          title: "Signature/Record Linking",
          body: `
            <p>Electronic signatures and handwritten signatures executed to electronic records must be
            linked to their respective electronic records to ensure that the signatures cannot be
            excised, copied, or otherwise transferred to falsify an electronic record by ordinary
            means.</p>
          `,
        },
      ],
    },
    {
      title: "Subpart C — Electronic Signatures",
      sections: [
        {
          ref: "§11.100",
          title: "General Requirements",
          body: `
            <ol type="a">
              <li>Each electronic signature shall be <strong>unique</strong> to one individual and shall not be reused by, or reassigned to, anyone else.</li>
              <li>Before an organization establishes, assigns, certifies, or otherwise sanctions an individual's electronic signature, the organization shall <strong>verify the identity</strong> of the individual.</li>
              <li>Persons using electronic signatures shall, prior to or at the time of such use, certify to the agency that the electronic signatures in their system are intended to be the legally binding equivalent of traditional handwritten signatures. The certification shall be submitted in paper form and signed with a traditional handwritten signature, to the Office of Regional Operations (HFC-100).</li>
            </ol>
          `,
        },
        {
          ref: "§11.200",
          title: "Electronic Signature Components and Controls",
          body: `
            <p><strong>(a) Electronic signatures not based upon biometrics</strong> shall:</p>
            <ol>
              <li>Employ at least two distinct identification components such as an identification code and password.</li>
              <li>When an individual executes a series of signings during a single, continuous period of controlled system access, the first signing shall be executed using all electronic signature components; subsequent signings shall be executed using at least one electronic signature component that is only executable by, and designed to be used only by, the individual.</li>
              <li>When an individual executes one or more signings not performed during a single, continuous period of controlled system access, each signing shall be executed using all of the electronic signature components.</li>
              <li>Be used only by their genuine owners.</li>
              <li>Be administered and executed to ensure that attempted use of an individual's electronic signature by anyone other than its genuine owner requires collaboration of two or more individuals.</li>
            </ol>
            <p><strong>(b) Electronic signatures based upon biometrics</strong> shall be designed to ensure that they cannot be used by anyone other than their genuine owners.</p>
          `,
        },
        {
          ref: "§11.300",
          title: "Controls for Identification Codes/Passwords",
          body: `
            <ol type="a">
              <li>Maintaining the <strong>uniqueness</strong> of each combined identification code and password, such that no two individuals have the same combination of identification code and password.</li>
              <li>Ensuring that identification code and password issuances are <strong>periodically checked, recalled, or revised</strong> (e.g. to cover such events as password aging).</li>
              <li>Following <strong>loss management procedures</strong> to electronically deauthorize lost, stolen, missing, or otherwise potentially compromised tokens, cards, and other devices that bear or generate identification code or password information, and to issue temporary or permanent replacements using suitable, rigorous controls.</li>
              <li>Use of <strong>transaction safeguards</strong> to prevent unauthorized use of passwords and/or identification codes, and to detect and report in an immediate and urgent manner any attempts at their unauthorized use to the system security unit, and, as appropriate, to organizational management.</li>
              <li><strong>Initial and periodic testing of devices</strong> (such as tokens or cards) that bear or generate identification code or password information to ensure that they function properly and have not been altered in an unauthorized manner.</li>
            </ol>
          `,
        },
      ],
    },
  ],
};
