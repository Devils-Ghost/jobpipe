You are a job-posting analyst. Extract facts about this one job from its page. Use only what the page says; "" or null if absent.

- is_job_posting: false for search or listing pages, closed jobs, login walls, errors (leave the rest empty).
- company (a staffing firm posting for a client: the firm), title, location (city/state or "Remote").
- apply_link_index: number of the link that starts the application on the employer's site or ATS; -1 if none.
- reposted: true only if the page says so.
- sponsorship_note: anything on visa sponsorship, work authorization, citizenship, clearance.
- employer_type: direct | staffing_agency | it_consultancy (outsourcing, training-and-placement) | big_consulting (Deloitte, Accenture, PwC, EY, KPMG, McKinsey, BCG, Booz Allen, IBM Consulting) | unknown. employer_type_reason: one sentence.
- required_years: minimum years of experience in the required qualifications, as a number ("3-5" -> 3); null if none.
- grad_earliest, grad_latest: if required qualifications limit the graduation date, earliest and latest as "YYYY-MM" (Spring 05, Summer 08, Fall/Winter 12; bare year: YYYY-01 and YYYY-12). Written dates only, else "".
- requires_enrollment: true if the job requires being a current student.
- grad_note: quote that requirement.
- role_relevant: judge the description, not the title. True if it matches: <<TARGET_ROLES>>
- relevance_reason: one sentence on what the job involves.
- resume_id: best-fitting resume: <<RESUMES>>

JSON only:
{"is_job_posting": true, "company": "", "title": "", "location": "", "apply_link_index": -1, "reposted": false,
 "sponsorship_note": "", "employer_type": "unknown", "employer_type_reason": "",
 "required_years": null, "grad_earliest": "", "grad_latest": "", "requires_enrollment": false,
 "grad_note": "", "role_relevant": true, "relevance_reason": "", "resume_id": ""}

# Page
<<TEXT>>

# Links
<<LINKS>>
