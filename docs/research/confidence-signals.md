# Confidence signals per backend

Answers issue #18, part of the wayfinder map #15. The question: for each
candidate backend, can a per-field confidence be read, at what granularity,
and does asking for it change price or disable structured output.

All dates read are 2026-09-14 unless a snippet quotes a page's own dated
content. Anything a source did not state outright is marked "unverified"
rather than assumed.

## Gemini 3.1 Flash-Lite, Interactions API

This is the engine's current backend (`engine/src/docmatch/extraction/gemini.py`),
called through `client.interactions.create` with `response_format`, not the
older `generate_content` call.

- **Signal:** none, as far as the Interactions API's own docs state. Neither
  https://ai.google.dev/gemini-api/docs/interactions nor
  https://ai.google.dev/gemini-api/docs/interactions-overview document a
  `logprobs` or `response_logprobs` field anywhere in `generation_config`
  (both read 2026-09-14; the config section only names `thinking_level` and
  `temperature` explicitly, with an unspecified "etc."). A Google AI
  Developers Forum thread, "Missing logprobs support in Next Gen
  'Interactions' API (GenerationConfig_2)"
  (https://discuss.ai.google.dev/t/missing-logprobs-support-in-next-gen-interactions-api-generationconfig-2/144837,
  read 2026-09-14), states the newer Interactions generation-config type
  omitted the token-logprob fields the older `generateContent` config carries.
  A forum thread is not an official doc, so treat it as corroboration of an
  absence, not proof: the Interactions API reference simply does not list
  the parameter, which is what a backend deciding whether to build on it
  needs to know either way.
- **Granularity, and how it would map to a field:** not applicable, since
  the signal does not exist on this endpoint. For contrast: on the legacy
  `generateContent` call, `responseLogprobs: true` plus an integer `logprobs`
  returns per-output-token log probabilities, described in Google's own
  logprobs blog post as covering "each token occurring in the sequence"
  (https://developers.googleblog.com/unlock-gemini-reasoning-with-logprobs-on-vertex-ai/,
  read 2026-09-14, a Vertex AI-focused post, not the Gemini API reference
  itself). Mapping token logprobs onto a specific JSON field would still
  require walking the token stream back onto the parsed JSON structure
  (matching key tokens, punctuation and value tokens to schema paths), the
  same manual alignment problem described for OpenAI below. Unverified
  whether this endpoint's absent-in-Interactions logprobs would have covered
  image-conditioned generation at all, since it cannot be tested there.
- **Image input + schema-constrained output, together:** unverified and
  moot, since logprobs are not documented on this endpoint regardless of
  what else is requested.
- **Price impact:** the pricing page (https://ai.google.dev/gemini-api/docs/pricing,
  read 2026-09-14, the same page `gemini.py` already cites) lists a flat
  input/output per-million-token rate for `gemini-3.1-flash-lite` and does
  not mention logprobs anywhere, so there is no documented price hook to
  interact with in the first place.

## OpenAI gpt-5-nano

- **Signal:** token logprobs, but the mechanism is not "with structured
  output" so much as "alongside it, unverified whether truly joint." The
  Responses API's `POST /responses` reference
  (https://developers.openai.com/api/docs/api-reference/responses/create,
  read 2026-09-14) does not accept a request-level `logprobs` parameter;
  logprobs are instead requested via `include: ["message.output_text.logprobs"]`
  and returned on `ResponseOutputText.logprobs` as an array of
  `{token, bytes, logprob, top_logprobs}` objects. The model's own capability
  page (https://developers.openai.com/api/docs/models/gpt-5-nano, read
  2026-09-14) lists `structured_outputs` and `image_input` among gpt-5-nano's
  supported features, but does not list logprobs as a feature at all (the
  capability list is silent on it, not a documented "no").
- **Granularity, and how it maps to a field:** token-level, returned as one
  entry per output token including JSON structural tokens (braces, quotes,
  keys, commas), per the schema above. Mapping a token's logprob onto one
  schema field means walking the character offsets implied by each token's
  `bytes` back onto the JSON text and matching them to the span the parser
  assigned to a given key's value, since the API gives token probabilities,
  not field probabilities. A community-built library,
  `structured-logprobs` (https://github.com/arena-ai/structured-logprobs,
  not an OpenAI source, cited here only to show the alignment problem is
  real and already worked around by others), does exactly this: it parses
  the structured JSON, then re-attaches each field's characters to the
  logprob array by offset.
- **Image input + schema-constrained output, together, with logprobs:**
  unverified for gpt-5-nano specifically. The official docs do not state
  whether `include: ["message.output_text.logprobs"]` returns populated
  values when `text.format` is a JSON schema. A live OpenAI Developer
  Community bug report, "GPT-5.1/5.2: message.output_text.logprobs is empty
  when Structured Outputs (json_schema) is enabled in Responses API"
  (https://community.openai.com/t/gpt-5-1-5-2-message-output-text-logprobs-is-empty-when-structured-outputs-json-schema-is-enabled-in-responses-api/1371927,
  a forum post, not an official statement, read 2026-09-14), reports the
  combination returning an empty array on adjacent GPT-5.x models. Whether
  this affects gpt-5-nano is unverified; it is at minimum a live risk worth
  testing rather than assuming away.
- **Price impact:** the pricing page
  (https://developers.openai.com/api/docs/pricing, read 2026-09-14) lists
  gpt-5-nano at $0.05 input / $0.40 output per million tokens with no
  logprobs-specific surcharge mentioned anywhere on the page.

## OpenAI gpt-4o-mini

- **Signal:** token logprobs via the Chat Completions API's documented
  `logprobs` (boolean) and `top_logprobs` (0-20) parameters
  (https://developers.openai.com/api/docs/api-reference/chat/create, read
  2026-09-14): "Whether to return log probabilities of the output tokens or
  not. If true, returns the log probabilities of each output token returned
  in the `content` of `message`." That reference page does not state an
  incompatibility with `response_format`/structured outputs or with image
  input, but it also does not confirm compatibility; the examples shown do
  not combine them. gpt-4o-mini's own capability page
  (https://developers.openai.com/api/docs/models/gpt-4o-mini, read
  2026-09-14) lists `image_input` and structured-outputs support ("produces
  text outputs, including Structured Outputs") but does not list logprobs
  among its supported features either, the same silent gap as gpt-5-nano.
- **Granularity, and how it maps to a field:** identical shape and mapping
  problem to gpt-5-nano above: one logprob per output token, JSON structure
  included, and no vendor-side mapping onto a schema path. Structured
  Outputs' own guide
  (https://developers.openai.com/api/docs/guides/structured-outputs, read
  2026-09-14) lists `gpt-4o-mini` and `gpt-4o-mini-2024-07-18` as supported
  models for the json_schema mode, but says nothing about logprobs there.
- **Image input + schema-constrained output, together, with logprobs:**
  unverified from the docs read. gpt-4o-mini predates the GPT-5.x line the
  community bug report above names, so that specific report does not speak
  to this model either way.
- **Price impact:** the pricing page lists gpt-4o-mini at $0.15 input /
  $0.60 output per million tokens, again with no logprobs-specific price
  line.

## Claude Haiku 4.5 and Claude Sonnet 5

Checked together: Anthropic's Messages API is one surface for both models,
and the answer is the same shape for each.

- **Signal:** none. The Messages API reference
  (https://platform.claude.com/docs/en/api/messages, read 2026-09-14) lists
  every top-level request parameter (`max_tokens`, `messages`, `model`,
  `cache_control`, `container`, `inference_geo`, `metadata`, `output_config`,
  `service_tier`, `stop_sequences`, `stream`, `system`, `thinking`,
  `tool_choice`, `tools`, and the deprecated `temperature`/`top_k`/`top_p`)
  and none of them is `logprobs` or `top_logprobs`; the response content
  block types (`TextBlock`, `ImageBlock`, `ToolUseBlock`, and so on) carry no
  log-probability or confidence field either. The Structured Outputs guide
  (https://platform.claude.com/docs/en/build-with-claude/structured-outputs,
  read 2026-09-14) describes `output_config.format` as constrained sampling
  against a compiled grammar and does not mention any confidence, logprob or
  probability field returned per output field.
- **Granularity:** not applicable; there is nothing to map. Unverified
  whether the absence is a deliberate design choice (constrained-decoding
  guarantees make a token-level signal less meaningful, since the grammar
  already forces validity) or simply a feature Anthropic has not shipped;
  the docs read do not say either way, and this file does not guess.
- **Image input + schema-constrained output, together:** both models are
  explicitly listed as supporting Structured Outputs on the same guide
  (`claude-sonnet-5` and `claude-haiku-4-5-20251001` both appear in its
  supported-models list). The guide states structured outputs can be used
  to "extract data from images or text," which implies vision compatibility
  without a page that states it as a flat yes; call that implied-but-not-
  definitively-confirmed rather than fully verified. Since no confidence
  signal exists regardless, this question is moot for the purpose asked.
- **Price impact:** no logprobs-adjacent line exists to price, since the
  parameter does not exist. Current list prices, cross-checked against the
  `claude-api` skill's cached model table (2026-06-24) rather than an
  independent pricing-page fetch this session: Claude Sonnet 5 $2.00 input /
  $10.00 output per million tokens, Claude Haiku 4.5 $1.00 input / $5.00
  output per million tokens.

## Azure AI Document Intelligence, prebuilt-invoice

- **Signal:** vendor field confidence, not logprobs. Document Intelligence
  is a purpose-built extraction service, not a general LLM, and it reports
  confidence as a first-class part of the response. The concept page
  (https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/concept/accuracy-confidence?view=doc-intel-4.0.0,
  read 2026-09-14) states: "Document Intelligence analysis results return an
  estimated confidence for predicted words, key-value pairs, selection
  marks, regions, and signatures... Field confidence indicates an estimated
  probability between 0 and 1 that the prediction is correct." The same page
  shows a screenshot captioned "confidence scores from Document Intelligence
  Studio: Analyzed invoice prebuilt-invoice model," tying the feature
  directly to this model. One caveat stated on the same page: "Currently,
  not all document fields return a confidence score," and its notes on
  table/row/cell confidence specifically flag "starting with the 2024-11-30
  (GA) API version for custom models," language the page does not repeat for
  prebuilt models. Whether prebuilt-invoice's line-item table cells carry
  their own confidence at that same 2024-11-30 API version, versus only
  document-level and header-field confidence, is unverified from the pages
  read in this session; the analyze-result REST reference page fetched
  truncated before reaching the full `DocumentField` schema.
- **Granularity:** confirmed at word level (a sample response snippet shows
  `{"content": "CONTOSO", "confidence": 1, ...}` per word, from
  https://learn.microsoft.com/en-us/rest/api/aiservices/document-models/get-analyze-result?view=rest-aiservices-2024-11-30,
  read 2026-09-14) and at field/key-value-pair level per the concept page
  above; table/row/cell confidence is documented as a feature of the format
  in general but its prebuilt-model scope is unverified as noted. Mapping
  onto the engine's schema fields would mean matching Azure's own field
  names (`VendorName`, `InvoiceTotal`, `CustomerAddress`, `Items[]`, and so
  on, per the invoice model schema page linked from
  https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/invoice?view=doc-intel-4.0.0)
  to DocILE's fieldtypes, a naming translation the current schema module
  deliberately avoids for the vision-LLM path (see `schema.py`'s own
  docstring on why fieldtypes are named after the labels).
- **Price impact:** not checked against the official pricing page this
  session; confidence is described everywhere as an ordinary part of the
  analyze response, not an opt-in feature, so there is no documented toggle
  to attach a price to. Mark the absence of a separate charge as inferred
  from how the feature is described, not confirmed against a pricing page
  read this session.

## Google Document AI, Invoice Parser

- **Signal:** vendor field confidence, returned as part of the `Document`
  proto's `entities[]`. The response-handling page
  (https://docs.cloud.google.com/document-ai/docs/handle-response, read
  2026-09-14) documents `entities[].confidence` as a float, shown in a
  sample as `"confidence": 0.9938466`, and gives Java/Python/Node accessor
  examples (e.g. `entity.getConfidence()`).
- **Granularity:** per entity. The same page discusses `normalizedValue`
  (holding typed sub-values like `dateValue`, `addressValue`, `text`) without
  stating whether the normalized value carries its own confidence separate
  from the parent entity's; that is unverified. It likewise shows nested
  entities via `properties[]` (how Invoice Parser represents line items)
  without stating whether each child property has an independent confidence
  distinct from its parent line item entity; also unverified from this
  page. Mapping onto the engine's schema would mean matching Document AI's
  own invoice entity types to DocILE's fieldtypes, the same translation
  problem noted for Azure above.
- **Price impact:** the official pricing page
  (https://cloud.google.com/document-ai/pricing) truncated before yielding
  the Invoice Parser's specific rate or any confidence-related pricing note
  in this session's fetch, so the price itself is unverified against the
  primary source here. No source read, official or otherwise, described
  confidence as a separately priced option; it appears to be a standard part
  of every entity, the same inference-not-confirmation caveat as Azure's.

## Docling with granite-docling

- **Signal:** document/page-level conversion-quality confidence, not a
  per-invoice-field confidence at all, and this is a real category
  difference the question's framing should not paper over. Docling's own
  concept page (https://docling-project.github.io/docling/concepts/confidence_scores/,
  read 2026-09-14) describes a `ConversionResult.confidence` object with
  page-level and document-level scores: `layout_score` ("overall quality of
  document element recognition"), `ocr_score` ("quality of OCR-extracted
  content"), `parse_score` ("10th percentile score of digital text cells,
  emphasizes problem areas"), and `table_score` (explicitly "not yet
  implemented"), each 0.0-1.0, rolled up into `mean_grade` and `low_grade`
  (5th percentile) and categorized as `POOR`/`FAIR`/`GOOD`/`EXCELLENT`. None
  of this is scoped to "is this `vendor_name` value correct": it measures
  how well the page as a whole was read, which is the OCR/layout-confidence
  category the DoclingDocument page itself distinguishes from a
  semantic-field confidence (the DoclingDocument concept page,
  https://docling-project.github.io/docling/concepts/docling_document/,
  read 2026-09-14, does not mention confidence at all; the feature lives on
  `ConversionResult`, one level up, not on the document model itself).
- **Granularity, and the VLM question specifically:** page and document
  level only; no per-element, per-cell, or per-OCR-word score is documented
  on this page. Whether `granite-docling`'s VLM pipeline (which replaces the
  classic layout-model-plus-OCR pipeline Docling otherwise runs) populates
  `layout_score`/`ocr_score`/`parse_score` at all is unverified: the concept
  page does not name which pipeline backends produce which scores, and a
  GitHub discussion asking about API-level confidence
  (https://github.com/docling-project/docling/discussions/2814, read
  2026-09-14) states plainly that "the API does not currently provide a
  confidence score for PDF processing results" and that "confidence metrics
  from OCR or layout analysis are not exposed in the API response," without
  distinguishing pipeline backends either. Since none of this is a per-field
  signal regardless, it cannot substitute for the Azure/Google-style vendor
  field confidence above even where it is populated.
- **Price impact:** not applicable; Docling is a local, open pipeline with
  no vendor per-call pricing to attach a confidence surcharge to.

## Summary table

| Backend | Signal | Granularity | With image + structured output, on this model | Price impact |
|---|---|---|---|---|
| Gemini 3.1 Flash-Lite (Interactions API) | none documented | n/a | n/a, logprobs absent from this endpoint | none found |
| OpenAI gpt-5-nano | token logprobs (`include: message.output_text.logprobs`) | per output token, needs manual JSON-offset mapping to a field | unverified; a live community bug report shows empty logprobs with Structured Outputs on adjacent GPT-5.x models | none found |
| OpenAI gpt-4o-mini | token logprobs (`logprobs`/`top_logprobs`) | per output token, same manual mapping problem | unverified from docs read | none found |
| Claude Haiku 4.5 | none | n/a | structured outputs plus vision both supported; no confidence signal to combine them with | none found |
| Claude Sonnet 5 | none | n/a | structured outputs plus vision both supported; no confidence signal to combine them with | none found |
| Azure Document Intelligence prebuilt-invoice | vendor field confidence | word-level confirmed, field/key-value confirmed, table/row/cell scope for prebuilt models unverified | vendor's own extraction path, not a general LLM call | none found, inferred not confirmed |
| Google Document AI Invoice Parser | vendor field confidence (`entities[].confidence`) | per entity confirmed; normalized-value and nested-property confidence unverified | vendor's own extraction path | official price unverified this session (page truncated); no confidence surcharge found |
| Docling + granite-docling | document/page conversion-quality score, not a field confidence | page and document level only | VLM-pipeline coverage of the score unverified; API itself currently exposes none | n/a, local pipeline |

## Sources read this session (2026-09-14 unless noted)

- https://ai.google.dev/gemini-api/docs/interactions
- https://ai.google.dev/gemini-api/docs/interactions-overview
- https://ai.google.dev/gemini-api/docs/pricing
- https://discuss.ai.google.dev/t/missing-logprobs-support-in-next-gen-interactions-api-generationconfig-2/144837 (forum, not official)
- https://developers.googleblog.com/unlock-gemini-reasoning-with-logprobs-on-vertex-ai/ (Google blog, Vertex-focused, not the Gemini API reference)
- https://developers.openai.com/api/docs/api-reference/responses/create
- https://developers.openai.com/api/docs/api-reference/chat/create
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://developers.openai.com/api/docs/models/gpt-5-nano
- https://developers.openai.com/api/docs/models/gpt-4o-mini
- https://developers.openai.com/api/docs/pricing
- https://community.openai.com/t/gpt-5-1-5-2-message-output-text-logprobs-is-empty-when-structured-outputs-json-schema-is-enabled-in-responses-api/1371927 (forum, not official)
- https://github.com/arena-ai/structured-logprobs (community library, not official, cited only to show the token-to-field mapping problem is real)
- https://platform.claude.com/docs/en/api/messages
- https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- Anthropic model pricing cross-checked against the `claude-api` skill's cached table (2026-06-24), not an independent pricing-page fetch this session
- https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/concept/accuracy-confidence?view=doc-intel-4.0.0
- https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/invoice?view=doc-intel-4.0.0
- https://learn.microsoft.com/en-us/rest/api/aiservices/document-models/get-analyze-result?view=rest-aiservices-2024-11-30
- https://learn.microsoft.com/en-us/rest/api/aiservices/document-models/analyze-document?view=rest-aiservices-2024-11-30
- https://github.com/Azure-Samples/document-intelligence-code-samples/blob/main/schema/2024-11-30-ga/invoice.md
- https://docs.cloud.google.com/document-ai/docs/handle-response
- https://cloud.google.com/document-ai/pricing (fetch truncated before pricing table; price itself unverified)
- https://docling-project.github.io/docling/concepts/confidence_scores/
- https://docling-project.github.io/docling/concepts/docling_document/
- https://github.com/docling-project/docling/discussions/2814

Also read locally, for the code the answer has to fit:
`engine/src/docmatch/extraction/schema.py`, `engine/src/docmatch/extraction/gemini.py`.
