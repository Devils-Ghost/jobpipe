You are a job-listing parser. List every job card below. Each starts with `=== JOB CARD (<url>) ===`.

Per job:
- company, title, location as shown.
- jobright_url: the card's URL, copied exactly.
- reposted: true only if the card says "Reposted".
- sponsorship_note: anything on H1B, visa, citizenship, clearance ("" if none).
- employer_type: direct | staffing_agency | it_consultancy (outsourcing, training-and-placement) | big_consulting (Deloitte, Accenture, PwC, EY, KPMG, McKinsey, BCG, Booz Allen, IBM Consulting) | unknown. employer_type_reason: one sentence.
- required_years: minimum years shown ("3+ years" -> 3); null if none.

JSON array only:
[{"company": "", "title": "", "location": "", "jobright_url": "", "reposted": false,
  "sponsorship_note": "", "employer_type": "direct", "employer_type_reason": "", "required_years": null}]

# Cards
<<TEXT>>

# Links
<<LINKS>>
