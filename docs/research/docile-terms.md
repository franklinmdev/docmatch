# DocILE terms and hosted APIs

Research for the Phase 1 map. Two reports, read from primary sources on 2026-09-14. Document titles and ids are scrubbed.

## Part 1. DocILE Dataset: License and Third-Party/API Terms, Research Report

### Method note

I read the request form directly (both via WebFetch and via a live browser render with agent-browser, which agreed verbatim), the GitHub repo's `LICENSE` and `README.md` via raw fetch, the full arXiv paper (v2, including Supplementary Material) via PDF read, the linked "Information on the processing of personal data" PDF, and the UCSF Industry Documents Library's Copyright/Fair Use and "IDL and AI" pages via live browser render. I could not find an official Rossum-published Hugging Face or Zenodo dataset card (see §4).

---

### 1. Exact clauses, verbatim

**a) GitHub repo license, `https://github.com/rossumai/docile/blob/main/LICENSE`**
This is the **MIT License**, and it applies to the `docile` Python library/tooling in the repo, not to the dataset content:

> "Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software..."

The README (`https://github.com/rossumai/docile/blob/main/README.md`) does not contain any separate dataset-license or terms-of-use text; it only points to `https://docile.rossum.ai/` for the access token.

**b) `https://docile.rossum.ai/`**, no "License" or "Terms" section exists on the page itself. It states, under "Competition and Prizes":

> "It is prohibited to use external document datasets and models trained on these datasets."

(This applies only to competition-prize eligibility, not to dataset use generally.) It links out to the request form and to a PDF titled "Legal information on the processing of personal data for the purpose of scientific research," but reproduces no license text itself.

**c) The Dataset Access Request form, `https://forms.gle/poJqGXrxoftWrUsc8`** (redirects to `https://docs.google.com/forms/d/e/1FAIpQLSeYaPkF_BOeD2GwBGueVbprESD7Mys-hMAiUj8oVKBmBGnJUw/viewform`). This is the actual terms a user agrees to when requesting a token. Read directly from the live rendered form, verbatim, under "Legal Notice":

> "The dataset can only be used for non-commercial research purposes. You must not use the content other than for this permitted purpose. **You must not provide or otherwise allow access to the content to any third party**, or attempt to identify any individual from the content. You must comply with all applicable laws and regulations, including without limitation the GDPR and the UK GDPR. All rights not expressly granted to you are reserved to us and/or our licensors. We reserve the right to withdraw the permission to use the content in the event of non-compliance with these terms."
>
> "You must not use the content if you do not agree to these terms."

And under the "Declare Purpose to Use the Data" checkbox the user must tick:

> "I agree to use the dataset for non-commercial research purposes only and to **not distribute it**. I will delete the content if and when the permission to use it ends."

**d) The personal-data-processing PDF, linked from docile.rossum.ai** (`https://docile.rossum.ai/static/information_duty_on_scientific_research.pdf`), issued by Rossum Ltd (UK) / Rossum Czech Republic s.r.o. Relevant clauses:

> "In order to create a scientific dataset, which will aid research and cross-evaluation of methods for document understanding using machine learning and AI, we integrated a large amount of publicly available business documents into a single public database."

> "We are therefore the controller of such personal data, whether the recipient and also user could be anyone on the internet."

> "Publicly gathered dataset is shared only as a response to particular requests for scientific research purposes."

This document is a GDPR Article 13/14 personal-data notice, not a license or a data-processing-agreement; it says nothing about processors, sub-processors, or sending data to third-party services.

**e) The DocILE arXiv paper, §3.2 "Data Sources" (arXiv:2302.05658v2)**, no licensing clause at all. It states only:

> "Documents in the DocILE dataset come from two public data sources: UCSF Industry Documents Library [80] and Public Inspection Files (PIF) [82]."

There is no "Ethics" or "Licensing" subsection in the paper, and no statement anywhere in the paper (main text or Supplementary Material) about redistribution, third-party access, or processing by external services.

**f) No dataset-terms file exists in the repo.** I listed the GitHub repo root (`LICENSE`, `README.md`, `Dockerfile`, `docile/`, `baselines/`, `tutorials/`, etc.), there is no `DATA_LICENSE`, `TERMS.md`, or similar file. The only binding dataset terms are the Google Form "Legal Notice" quoted in (c).

---

### 2. Does any clause address sending data to an external service for inference?

**No clause anywhere, the form, the website, the GitHub repo, the paper, or the personal-data PDF, mentions APIs, cloud inference, model providers, sub-processors, or "processing on your behalf by a service."** The only operative restriction is the form's plain-language "must not provide or otherwise allow access to the content to any third party" and "not to distribute it," with no elaboration of what counts as a "third party" or an exception for processing.

### My interpretation (clearly separated from the sourced facts above)

Sending page images to Gemini, OpenAI, Anthropic, or Azure Document Intelligence for inference means uploading the document content to those companies' servers, which are legally distinct entities from Rossum and from you. Under an ordinary reading of "you must not provide or otherwise allow access to the content to any third party," a commercial API provider is a third party being given access to the content, there's no processor/sub-processor carve-out in the text, unlike, e.g., a typical DPA that would explicitly exempt "processors acting on your instructions." I would not assume such a carve-out exists just because it's common in other agreements; this text simply doesn't have one. So my reading is that literal-third-party-access clause plausibly *does* cover API inference, and the safer assumption for `docmatch` is that it does, unless Rossum confirms otherwise. This is my judgment call, not something the primary sources state.

That said, there's genuine ambiguity worth flagging to Franklin: the clause's evident purpose (per the personal-data PDF) is to stop re-identification of personal data and re-distribution of the raw corpus, not obviously to forbid ordinary ML-pipeline operations like OCR/LLM inference calls that a great many research users of the dataset almost certainly do anyway. But "the purpose was probably narrower" is not the same as "the clause exempts this", the text as written is unqualified.

**Provider retention/training terms**, since retention affects how much the "third party access" risk resembles distribution:

- **OpenAI API** (`https://platform.openai.com/docs/guides/your-data`, redirects to `https://developers.openai.com/api/docs/guides/your-data`): "data sent to the OpenAI API is not used to train or improve OpenAI models (unless you explicitly opt in to share data with us)." Abuse-monitoring logs are retained up to 30 days by default; this is explicitly distinguished from consumer ChatGPT policy.
- **Anthropic API** (`https://privacy.claude.com/en/articles/7996868-is-my-data-used-for-model-training`): "By default, we will not use your inputs or outputs from our commercial products (e.g. Claude for Work, Anthropic API, Claude Gov, etc.) to train our models," unless you opt in via feedback. Retention specifics point to Anthropic's Commercial Terms (`https://www.anthropic.com/legal/commercial-terms`) and Trust Center, which I did not fetch, mark general retention period as unverified.
- **Gemini API** (`https://ai.google.dev/gemini-api/terms`), explicitly two different regimes:
  - *Unpaid/free tier*: "Google uses the content you submit to the Services and any generated responses to provide, improve, and develop Google products and services," and "Human reviewers may read, annotate, and process your API input and output," with a warning: "Do not submit sensitive, confidential, or personal information to the Unpaid Services." This tier is a clear no-go for DocILE content given the personal-data categories (names, addresses, payment/bank details) the dataset's own personal-data notice admits may be present.
  - *Paid tier*: "Google doesn't use your prompts (including associated system instructions, cached content, and files such as images, videos, or documents) or responses to improve our products," and logs prompts/responses only "for a limited period of time, solely for detecting and preventing violations of the Prohibited Use Policy... and any required legal or regulatory disclosures."
- **Azure Document Intelligence** (`https://learn.microsoft.com/en-us/legal/cognitive-services/document-intelligence/data-privacy-security`, canonical at `.../azure/foundry/responsible-ai/document-intelligence/data-privacy-security`): "The service stores submitted input data and analyze results for 24 hours after an analysis operation completes. Document Intelligence automatically deletes both after this retention period." This page does not itself state whether input data is used for training, I did not find that sentence on this specific Document Intelligence page (flag as **unverified** for Document Intelligence specifically).
- **Azure OpenAI / "Models sold by Azure"** (`https://learn.microsoft.com/en-us/legal/cognitive-services/openai/data-privacy`, canonical `.../azure/foundry/responsible-ai/openai/data-privacy`), a related but distinct Azure AI service, quoted for context: "Your prompts (inputs) and completions (outputs)... are NOT used by providers of Models sold by Azure to improve their models or services... are NOT used to train any generative AI foundation models without your permission or instruction." And: "The models are stateless: no prompts or completions are stored in the model. Additionally, prompts and completions are not used to train, retrain, or improve the base models." Human review only occurs for flagged abuse-monitoring samples.

None of these provider policies change what DocILE's own terms say, a provider's "we won't train on it" promise doesn't retroactively make the provider not a "third party" under Rossum's form language. That equivalence is my inference, not a stated legal conclusion, and it's the crux of the compliance judgment call for `docmatch`.

---

### 3. Are the underlying UCSF Industry Documents public, and under what terms?

Yes, publicly accessible, but **not public domain**, under a copyright/fair-use framework, not an open license. From `https://www.industrydocuments.ucsf.edu/about-idl/copyright/` ("Copyright and Fair Use"), read directly:

> "Although the Industry Documents Library is a public archive of documents and audiovisual materials, the companies or individuals who created the information may still hold the rights, meaning material cannot be 'substantially' reproduced in books or other media without the copyright holder's permission."

> "The Industry Documents Library makes its collections available under court-approved agreements with the rights holders or legal precedent, depending on the collection."

> "Each user of this website is responsible for ensuring compliance with applicable copyright laws. Persons obtaining, or later using, a copy of copyrighted material in excess of 'fair use' may become liable for copyright infringement. By accessing this website, the user agrees to hold harmless the University of California, its affiliates and their directors, officers, employees and agents from all claims and expenses..."

So: publicly viewable (largely tobacco-industry litigation documents released under court-approved settlement agreements), governed by US fair-use doctrine on a case-by-case basis, with UCSF disclaiming responsibility and requiring users to self-assess fair use. The Public Inspection Files (the other DocILE source, FCC political ad files) are separately public U.S. government/broadcaster records under FCC rules, I did not pull FCC's specific terms page; treat that half as **unverified** for precise terms, though FCC political-file records are widely treated as public records.

Separately, UCSF's "IDL and AI" page (`https://www.industrydocuments.ucsf.edu/about-idl/idl-and-ai/`) describes UCSF's *own* use of AI tools on the archive (OCR, redaction assistance, transcript generation, an internal "data-secure and user-private AI platform (powered by ChatGPT)"), but says nothing about what downstream users of documents retrieved via the API may or may not do with third-party AI services. It is not a term of use for re-users.

---

### 4. Could not verify ("unverified")

- **No official Rossum/rossumai Hugging Face or Zenodo dataset card exists** that I could find. A Hugging Face search turned up only third-party community uploads (`ZaNioxX/DocILE_10_5_ImageClassification_donut`, `Humayoun/DocILE100`), neither is published by Rossum, neither carries a Rossum-authored license field, and their existence may itself be a violation of the "must not distribute" clause. I did not inspect their card text in depth since they are not primary sources for Rossum's terms. **Unverified: whether these mirrors are authorized, and what license (if any) they claim.**
- **FCC Public Inspection Files' own terms of use**, not fetched; unverified precise wording, though these are federal broadcaster political-file records generally treated as public.
- **Anthropic's exact retention period for API data**, the model-training page points to the Commercial Terms/Trust Center rather than stating a number itself; I did not fetch those pages. **Unverified.**
- **Whether Azure AI Document Intelligence specifically (as opposed to Azure OpenAI models) states a "not used for training" commitment**, the Document Intelligence-specific privacy page I fetched covers retention (24 hours) and storage/encryption, but the "not used to train" sentence I found was on the separate Azure OpenAI data-privacy page, not the Document Intelligence one. **Unverified for Document Intelligence specifically**, worth a follow-up fetch of Microsoft's Document Intelligence-specific responsible-AI/transparency note if this matters for a compliance decision.
- I could not "submit" the Google Form (by design, per your instruction not to guess at gated content), but I was able to read its full visible content, including the Legal Notice, via a rendered (non-submitted) view, so that clause is verified verbatim, not guessed.

---

### Bottom line for `docmatch`

The dataset's only binding usage terms are in the Google Form's "Legal Notice": non-commercial research only, no redistribution, and **"must not provide or otherwise allow access to the content to any third party."** Nothing in any primary source carves out API inference. My reading is that this clause, taken literally, covers sending DocILE page images to Gemini/OpenAI/Anthropic/Azure, this is an interpretation, not a stated rule, and CLAUDE.md's rule 6 ("Never commit datasets, document contents, or personal data... DocILE is licensed for non-commercial research use only, with no redistribution and no third-party access") already reflects this same conservative reading. Given the ambiguity, if `docmatch`'s Phase 1 extraction plan depends on sending DocILE images to a hosted VLM API, that's a decision worth flagging to Franklin explicitly rather than assuming it's fine, it directly touches the "no third-party access" boundary the project's own CLAUDE.md already worries about.
## Part 2. Report: DocILE hosted-VLM practice and original-source availability

All findings below come from primary sources fetched directly (arXiv/CEUR PDFs, the CVC Robust Reading Competition site, GitHub via `gh api`, Semantic Scholar's API, the UCSF Industry Documents Library and FCC Public Inspection Files sites via agent-browser/curl). Everything is quoted verbatim with its URL. Anything I could not confirm is marked "unverified." No legal conclusions are drawn, only what the sources say.

### LINE A: Is sending DocILE pages to hosted models an accepted practice?

### A1. Published papers evaluating hosted commercial models on DocILE

**Found: Eliott Thomas, Mickael Coustaty, Aurélie Joseph, Tri-Cong Pham, Gaspar Deloin, Elodie Carel, Vincent Poulain d'Andecy, Jean-Marc Ogier, "Zero-Shot Table Extraction in Business Documents: A Unified Benchmark with Error Taxonomy and Ecological Analysis," WACV 2026.**
PDF: https://openaccess.thecvf.com/content/WACV2026/papers/Thomas_Zero-Shot_Table_Extraction_in_Business_Documents_A_Unified_Benchmark_with_WACV_2026_paper.pdf (IEEE Xplore listing: https://ieeexplore.ieee.org/document/11492040/)

- Authors: Thomas, Joseph, Deloin, Carel, Poulain d'Andecy are affiliated with **Yooz, France** (a commercial intelligent-document-processing vendor); Coustaty and Pham with **La Rochelle Université, France**.
- **Mickael Coustaty is a co-author of the original DocILE paper** (affiliation "University of La Rochelle" in the DocILE paper, confirmed below in A2).
- Exact models: "evaluating commercial VLMs (GPT-4o, GPT-5-mini), compact detectors, and supervised YOLO/DETR baselines."
- Verbatim on methodology: "For dual-task models (GPT-4o, GPT-5-mini), we use OCR for TD and Image for TSR... For TSR, Image inputs were effective and cost-efficient with GPT-4o/5-mini.", i.e., page images were sent to GPT-4o/GPT-5-mini via API for the table-structure-recognition task.
- Verbatim on the dataset: "DocILE-QUEST [28] is a public dataset of English single-page documents, originally introduced for line-item recognition and later adapted for table extraction... We use the dataset as released for reproducibility... The dataset is publicly available on GitHub [27]." Reference [28] is the same authors' own prior paper: "Quest: Quality-aware semi-supervised table extraction for business documents," arXiv:2506.14568 (2025).
- Verbatim on API/emissions accounting: "For remotely hosted VLMs, we use Ecologits [8], which estimates emissions from API usage and provider parameters," and "commercial API systems that offer strong zero shot performance and ease of integration... managed services that reduce operational overhead but raise cost and governance considerations."
- The paper never uses the words "OpenAI," "Azure," or "Rossum," and does not discuss the DocILE license terms.

Note: "DocILE-QUEST" is a **derivative dataset the same authors created and republished themselves** (not the raw DocILE files redistributed by Rossum). I could not verify (unverified) whether DocILE-QUEST's images are DocILE's original licensed page images or synthetically/independently regenerated, since I did not fetch arXiv:2506.14568 itself.

Other candidate papers surfaced via Semantic Scholar's citation graph for the DocILE paper (arXiv:2302.05658) that mention GPT/Gemini/VLM terms but were **not verified in detail** for actually running on DocILE with a hosted API (time did not permit deep-reading all of them): "MMDocBench: Benchmarking Large Vision-Language Models for Fine-Grained Visual Document Understanding" (arXiv:2410.21311, 2024) evaluates GPT-4o/GPT-4V/Gemini 1.5 Pro on multiple document benchmarks but I did not confirm DocILE is one of its source benchmarks, **unverified**. "Document Intelligence in the Era of Large Language Models: A Survey" (arXiv:2510.13366, 2025) is a survey that likely discusses DocILE and LLM/VLM approaches generally but I did not extract a specific DocILE+hosted-API claim from it, **unverified**.

No paper was found where an author is affiliated with Rossum itself and which runs a hosted API on DocILE.

### A2. Has Rossum used/recommended hosted LLM/VLM APIs on DocILE?

- Rossum's own primary blog post on its "Aurora" product (https://rossum.ai/blog/rossum-aurora/) states: "powered by our proprietary Transactional Large Language Model (T-LLM), trained on millions of richly annotated documents - the largest data set of the kind in the industry." **This page does not mention the DocILE dataset/benchmark by name and does not show a GPT-4 benchmark or methodology.**
- A secondary industry-analysis source (deep-analysis.net, https://www.deep-analysis.net/rossum-launches-its-own-llm/) claims: "Rossum ran a benchmark against both OpenAI's GPT-4 and DocLLM" and that T-LLM was "trained on one of the largest document data sets in the world, Rossum's own DOCile." I could not access this page directly (403 Forbidden) to quote it myself; I only have WebSearch's summary of it, and I could not find Rossum's own primary-source writeup of this specific GPT-4 comparison. **This claim, and whether "DOCile" there means the public DocILE benchmark or an unrelated internal corpus with a similar name, is unverified.**
- The DocILE overview papers (below, A4) show the paper's Rossum co-authors (Šimsa, Uřičář) did not themselves use or recommend hosted APIs in that publication.

### A3. GitHub issues/discussions at rossumai/docile

- The repository has **no Discussions tab** (`has_discussions: false`, via `gh api repos/rossumai/docile`).
- Searched all issues via GitHub's search API for "GPT," "API," "commercial," "third party," "license" (`gh api "search/issues?q=repo:rossumai/docile+..."`). Only license-related issues exist: #51 "Dataset License?", #38 "Add LICENSE file," #53 "Dockerfile: copy LICENSE to correct location."
- Issue #51 ("Dataset License?"), opened by logan-markewich: "On the website, it states that the dataset is for research purposes. Does this mean commercial applications are blocked from using the dataset? Or what is the specific licensing?", self-answered by the same user with no maintainer reply: "Ah, I see on the dataset signup form that it is clearly for research only. That is rather unfortunate, but understandable."
- Issues #81/#82 ("access to data") got a reply from Rossum's Štěpán Šimsa (GitHub handle `simsa-st`), but only about retrieving the access token, not about license or API scope: "Hi Dorsa, when you fill the form, after submitting, it shows you the token right away, you have to copy it at that point."
- No issue anywhere mentions GPT, hosted APIs, or third-party processing.

### A4. ICDAR 2023 / CLEF 2023 competition rules and participant reports

- Official rules PDF (https://docile.rossum.ai/static/docile_rules_and_prize_eligibility.pdf), Benchmark Rule 2, verbatim: **"The use of external document datasets is prohibited along with models trained on these datasets. Other external datasets and checkpoints (ImageNet, Wikipedia text corpuses, ...) can be used as long as they are publicly available."** This rule is silent on hosted commercial LLM/VLM APIs specifically, it only restricts training on other *document* datasets.
- Competition page (https://rrc.cvc.uab.es/?ch=26&com=introduction), verbatim: "It is prohibited to use external document datasets and models trained on these datasets."
- The official competition report, "Extended Overview of DocILE 2023: Document Information Localization and Extraction" (CEUR-WS Vol-3497, https://ceur-ws.org/Vol-3497/paper-049.pdf; authors Šimsa, Uřičář (Rossum), Šulc (Second Foundation), Patel, Matas (CTU Prague), Hamdi, Doucet, Coustaty (La Rochelle), Karatzas (CVC Barcelona)) states: **"Usage of external document datasets or models pre-trained on such datasets was forbidden in the competition, while datasets and pre-trained models from other domains, such as images from ImageNet [28] or texts from Boo[ks]..., [were allowed]."** Only 5 teams submitted; the paper describes each method by name (e.g., "GraphDoc, USTC-iFLYTEK, China," "LiLT, University of Information Technology, Vietnam"), and I found **zero mentions of GPT, Azure, Document AI, Gemini, Claude, or OpenAI** in the full extracted text of this report, all submitted methods were trained/fine-tuned models (GraphDoc, LiLT, LayoutLM-family, RoBERTa), not hosted commercial APIs. This is consistent with the 2023 timeframe, before GPT-4V-style multimodal APIs were mainstream for such competitions.

### Bottom line, Line A

The only concrete, verified case of a hosted commercial VLM (GPT-4o, GPT-5-mini) being run on DocILE-derived document images that I could confirm is the WACV 2026 paper by Thomas et al., which includes a DocILE co-author (Coustaty) and used a self-published derivative dataset ("DocILE-QUEST") rather than the raw DocILE files. The 2023 competition rules and the official competition report are silent on hosted APIs (they only restrict external *document datasets and models trained on them*), and none of the 2023 competition's five submissions used hosted APIs. No GitHub issue, Rossum blog post, or the DocILE paper itself explicitly endorses or forbids using hosted commercial APIs on the licensed dataset. The claim that "Rossum itself benchmarked GPT-4 on DocILE" rests on a secondary source I could not verify and is marked unverified.

### LINE B: Are the same documents available from their original public sources?

### B1. UCSF Industry Documents Library, fetch by id

Both example ids resolve to real, viewable documents on the live public site:
- `https://www.industrydocuments.ucsf.edu/docs/<ucsf-id-1>` → redirects to `https://www.industrydocuments.ucsf.edu/all-industries/documents/viewer/?iid=<ucsf-id-1>&id=<ucsf-id-1>...`, a titled invoice document with collection and Bates metadata. The viewer has visible **Metadata**, **Cite**, **Download**, and **Share** buttons (confirmed via agent-browser snapshot of the interactive elements).
- `https://www.industrydocuments.ucsf.edu/docs/<ucsf-id-2>` → a titled invoice document, same viewer UI with a Download button present.
- I did not click Download or extract any document body content, per instructions, only page title/breadcrumb metadata and UI affordances were observed.
- API: https://www.industrydocuments.ucsf.edu/research-tools/api/ states: "The Industry Documents Library uses Solr to index the document corpus. Users who are interested in accessing the document metadata and searching the full text of the documents programmatically can query the Industry Documents Solr server directly through our application programming interface (API)... Search results can be exported in these formats: xml, json, python, ruby, php, and csv." This is a **metadata/full-text search API**, not confirmed to return raw PDFs directly (the DocILE paper cites this same API as its documented source for pulling invoice-type documents, see B4/paper citation).

### B2. FCC Public Inspection Files, resolve by id

- Developer docs: https://publicfiles.fcc.gov/developer, base URL `/api/manager`, requires an `accessToken` header for management operations. It exposes `GET /file/id/{fileId}.{format}` ("Get File Details… Returns the file information for the specified file id") which takes `fileId` (path) plus `entityId` (query, "Unique Entity Id").
- I called this live endpoint with the exact id given: `GET https://publicfiles.fcc.gov/api/manager/file/id/<pif-id>.json` → response: `{"status":"error","statusCode":400,"statusMessage":"Invalid Entity ID"}`. Adding an empty `entityId` param gave: `{"status":"error","statusCode":400,"statusMessage":"Invalid EntityId. Must be alphaNumeric and hyphen only"}`. **The API accepted the file-id format without complaint and only failed on the missing/invalid companion entity id**, meaning a bare "pif" id alone (as recorded in DocILE's metadata) is **not sufficient** to fetch the file through this API; a station/entity id must also be known. I could not determine that second id, so I could not confirm this specific document resolves to content, **unverified** whether it currently exists in the live system (files can also be deleted/archived over time).
- Public-facing (non-API) URLs do exist in the pattern `https://publicfiles.fcc.gov/{service}-profile/{callsign}/{category}/{uuid}` (e.g., an unrelated example found: `.../am-profile/wjay/issues-and-programs-lists/ae844ffc-77d0-7298-d58b-8aec6a0def2d`), which suggests documents are addressable once the callsign/category prefix is known, but I did not find or verify the specific prefix for `<pif-id>`.

### B3. Terms of use

**UCSF Industry Documents Library**, no page titled simply "Terms of Use" was found; the governing page is "Copyright and Fair Use" (https://www.industrydocuments.ucsf.edu/about-idl/copyright/), quoted in full relevant part: "Although the Industry Documents Library is a public archive of documents and audiovisual materials, the companies or individuals who created the information may still hold the rights, meaning material cannot be 'substantially' reproduced in books or other media without the copyright holder's permission... The Industry Documents Library makes its collections available under court-approved agreements with the rights holders or legal precedent, depending on the collection... Each user of this website is responsible for ensuring compliance with applicable copyright laws. Persons obtaining, or later using, a copy of copyrighted material in excess of 'fair use' may become liable for copyright infringement. By accessing this website, the user agrees to hold harmless the University of California..." **No language anywhere on this page addresses AI/ML processing or sending content to third-party services.**

**FCC Public Inspection Files**, the developer page links to "API usage terms and conditions" at `http://www.fcc.gov/developer/api-terms-of-service` and "FCC.gov Website Policies and Privacy Policy" at `http://www.fcc.gov/encyclopedia/privacy-policy`. Both the live `fcc.gov` versions of these pages returned **"Access Denied"** (Akamai bot-block) via agent-browser and a 403 via curl with a browser user-agent, and the Wayback Machine was temporarily offline when I tried a snapshot. **The exact current terms text could not be retrieved, unverified.** The developer page itself does state (quoted above in B2): "Our APIs are free for use by anyone subject to your acceptance of the API usage terms and conditions... If you use our APIs, please indicate the source on your page."

### B4. Does the DocILE paper say the PDFs are unmodified or re-rendered/cropped?

The DocILE paper (arXiv:2302.05658, "DocILE Benchmark for Document Information Localization and Extraction," Šimsa et al.) explicitly states the source PDFs are **modified**, in its Supplementary Material, "1.1 Document Preprocessing," verbatim:

> "The following pre-processing was applied to documents from the annotated set: - (UCSF only) PDFs from UCSF contain a text 'Source: [URL in UCSF]' on the bottom of the page. As this text would affect the competition tasks, it was removed from the PDFs. Instead, the original document ID is recorded in the dataset metadata. - Skewed pages were detected in PDFs and images rendered from the PDF pages were deskewed automatically. New PDFs were generated from the deskewed images, each rendered to have the longer dimension equal to 842 pixels (this corresponds to the longer side of A4 at 72 DPI)."

Also relevant, on sourcing: "For UCSF IDL, we used the public API [81] to retrieve only publicly available documents of type invoice. For documents from PIF, we retrieved all 'political files' from tv, fm and am broadcasts," where reference [81] is cited as "Web: Industry Documents Library API. https://www.industrydocuments.ucsf.edu/research-tools/api/" and reference [82] as "Web: Public Inspection Files. https://publicfiles.fcc.gov/." The GitHub README (fetched via `gh api repos/rossumai/docile/readme`) contains no additional statement on this and no mention of "GPT," "API," or "third party."

### Bottom line, Line B

Both example UCSF ids resolve on the live public UCSF site to real, individually viewable/downloadable invoice documents; UCSF's own governing page for reuse is a copyright/fair-use notice, not a full "terms of use," and it says nothing about AI or third-party processing (current text confirmed by direct read; FCC's equivalent terms page could not be reached, so that side is unverified). The FCC Public Inspection Files API accepted the given file-id format but requires a companion entity/station id the DocILE metadata does not provide by itself, so I could not confirm the specific `pif` example resolves to a document today. The DocILE paper itself confirms, in its own words, that the shipped PDFs are **not unmodified copies** of the originals: UCSF-sourced text was stripped, and pages were deskewed and re-rendered into new PDFs at a fixed resolution.

### Files and scratch data

Downloaded PDFs/text extracts used for the verbatim quotes above are in the session scratchpad, not the repo: `/tmp/claude-1000/-home-franklinmdev-docmatch/c1e82f5d-cb15-45a3-b249-80b9a8c2ae5b/scratchpad/` (`docile_paper.txt`, `docile_overview.txt`, `rules.pdf`, `thomas_wacv2026.txt`, `rrc_intro.html`, `fcc_dev_full.txt`, etc.). Nothing was written into the docmatch repo.