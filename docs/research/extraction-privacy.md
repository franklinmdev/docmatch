# Extraction options for private comprobantes (ticket #209)

Part of map #205. Read on 2026-10-22. Every claim carries its URL; "unverified" marks what no primary source pinned. No private document was used, and no model was run on any document.

## What the engine does today (read from the code and README only)

- Gemini: `client.interactions.create(model=..., input=..., response_format=...)` with `gemini-3.1-flash-lite`, `google-genai` 2.23.0 (`engine/src/docmatch/extraction/gemini.py`). No `store` argument is passed. Prices are quoted at the paid tier.
- Azure: `prebuilt-invoice`, API version `2024-11-30`, on an S0 resource (`extraction/azure.py`, README). The resource region is not stated in the repo files read: unverified here, check the portal.
- OpenAI: `client.responses.create(model="gpt-5.6-luna", input=..., text=...)` with openai-python 3.14.1 (`extraction/openai.py`). No `store` argument is passed.
- Which Google and OpenAI accounts have billing enabled is not stated in code or README: unverified. The Gemini terms below differ sharply by tier.

## Part 1. Vendor terms

### Gemini API (Interactions API, paid tier)

| Question | Finding | Source |
|---|---|---|
| Training use, paid | "Google doesn't use your prompts ... or responses to improve our products." Processed under the Data Processing Addendum. | https://ai.google.dev/gemini-api/terms (read 2026-10-22) |
| Training use, unpaid | Content and responses are used to "provide, improve, and develop" products, and "human reviewers may read, annotate, and process your API input and output." Terms say not to submit sensitive or personal information. | same |
| Abuse monitoring | Paid: prompts and responses are logged "for a limited period of time" solely to detect Prohibited Use violations. Retention is 55 days; access only by authorized Google employees for human review. Whether it can be turned off: not stated. | https://ai.google.dev/gemini-api/terms ; https://ai.google.dev/gemini-api/docs/abuse-monitoring (read 2026-10-22) |
| Interactions API storage | `store` defaults to true. Stored interactions are retained 55 days on the paid tier, 1 day on free. `store=false` disables it (incompatible with background execution and `previous_interaction_id`). | https://ai.google.dev/gemini-api/docs/interactions (read 2026-10-22) |
| Zero data retention | The ZDR page says nothing about a request or enrollment process for the Gemini Developer API. It says "If your workload requires guaranteed zero data retention or enterprise data processing agreements, use Vertex AI." For the Developer API it says you must explicitly set `store=false` on the Interactions API and avoid File API, explicit caching, Grounding with Google Search (30 days, cannot be disabled), and Live API session resumption. Implicit in-memory caching (24 h RAM) "does not violate zero data retention." Even then, the 55-day abuse log still applies (the page does not exempt it). | https://ai.google.dev/gemini-api/docs/zdr (read 2026-10-22) |
| Region | Terms: data "may be stored transiently or cached in any country in which Google or its agents maintain facilities." No residency commitment. The available-regions page lists where the API can be called, not where data is processed. | https://ai.google.dev/gemini-api/terms ; https://ai.google.dev/gemini-api/docs/available-regions (read 2026-10-22) |
| Vertex AI alternative | Reported by a search summary of Google's Vertex data-governance page (https://docs.cloud.google.com/vertex-ai/generative-ai/docs/data-governance): abuse-monitoring exceptions can be requested by Google Cloud customers. The fetched ZDR page points to it. I did not read the Vertex page itself: unverified in detail. | |

Consequence for docmatch: the engine's current call stores every document's prompt and response for 55 days (`store` defaults to true) and, whatever it does, Google keeps an abuse log for 55 days. `store=false` removes the first only.

### Azure AI Document Intelligence prebuilt-invoice (S0)

| Question | Finding | Source |
|---|---|---|
| Retention | "The service stores submitted input data and analyze results for 24 hours after an analysis operation completes" and deletes both automatically. The "Delete Analyze Result" API deletes them earlier. | https://learn.microsoft.com/en-us/azure/ai-foundry/responsible-ai/document-intelligence/data-privacy-security (page dated 2026-07-22, read 2026-10-22) |
| Region | "The incoming data is processed in the same region where the Document Intelligence resource was created." Temporary storage is in the same region, shared across customers in that region but logically isolated. | same |
| Training use | The data-privacy page and the transparency note (https://learn.microsoft.com/en-us/azure/foundry/responsible-ai/document-intelligence/transparency-note, read 2026-10-22) do not state whether customer input trains prebuilt models. Only Microsoft Q&A forum replies say it does not, which are not a primary source. Unverified from primary sources; the Microsoft Products and Services DPA was not read. | |
| Abuse monitoring / human review | Not mentioned on the pages read. Unverified. | |
| Zero data retention | No program is described. The fixed 24-hour auto-delete plus the delete API is the nearest control. A customer cannot shorten it below "delete after completion" except by calling the delete API. | same |

### OpenAI Responses API (`gpt-5.6-luna`)

| Question | Finding | Source |
|---|---|---|
| Training use | API data "is not used to train or improve OpenAI models" unless you explicitly opt in. | https://developers.openai.com/api/docs/guides/your-data (read 2026-10-22) |
| Response storage | `store` defaults to true when omitted; response data is stored "for at least 30 days." Docmatch does not pass `store`, so it stores every response. | openai-python 3.14.1 docstring for `store` in `.venv/.../openai/resources/responses/responses.py` (vendor SDK, read 2026-10-22); https://developers.openai.com/api/docs/guides/your-data |
| Abuse monitoring logs | Retained "up to 30 days" by default, longer if law requires or to protect the service. | https://developers.openai.com/api/docs/guides/your-data |
| Zero data retention | Not automatic. Needs prior OpenAI approval and extra requirements; contact OpenAI sales. Once approved, set in Settings, Organization, Data controls, Data retention, at org or project level. With ZDR, `/v1/responses` `store` is always treated as false. `/v1/responses` is ZDR-eligible "with limitations". Modified Abuse Monitoring is an alternative with the same approval path. | same |
| Region | Data-at-rest residency: US, Europe (EEA + Switzerland), Australia, Canada, Japan, India, Singapore, South Korea, UK, UAE. In-region inference only in US, Europe, UAE. Non-US regions need approval. Use `eu.api.openai.com` or `us.api.openai.com`. Which region the account uses: unverified. | same |

Not read: https://openai.com/policies/business-terms/ and https://openai.com/enterprise-privacy/ both returned HTTP 403 to the fetcher.

### Summary of the hosted options for a private comprobante

| Backend | Input kept | Abuse log | Trains on it | Can reach zero retention |
|---|---|---|---|---|
| Gemini, paid, `store` default | 55 days | 55 days | no (paid) | not on the Developer API; Vertex AI is the route Google names |
| Gemini, `store=false` | 0 stored by the API | 55 days | no (paid) | not fully; abuse log remains |
| Azure S0 | 24 h, deletable at once | not stated | not stated on primary pages | delete API; no formal ZDR |
| OpenAI, `store` default | at least 30 days | up to 30 days | no | yes, by approval only |
| OpenAI, `store=false` | not stored | up to 30 days | no | approval removes the log |

## Part 2. Local open-weight vision models

### This machine (measured 2026-10-22)

- `nproc`: 16 logical CPUs (AMD Ryzen 7 5800H).
- `free -g`: 27 GiB total, about 16 GiB available at the time, 54 GiB swap.
- `lspci`: NVIDIA GeForce RTX 3050 Ti Laptop GPU (nvidia-smi reports 4096 MiB) and an AMD Cezanne Vega iGPU.

The ticket assumed 10 CPUs, about 11 GiB and no GPU. That is the old WSL machine; this one is larger and has a 4 GiB NVIDIA GPU. Whether CUDA drivers and runtimes work here was not tested.

### Candidates

No model below has a published CPU latency, and none has a published SROIE or CORD or DocILE score on its model card or paper read here. Latency on this machine must be measured, on public sample receipts only. I give no estimates, since none are published.

| Model | Size | License | Runtime | Published accuracy (document tasks) | Published speed |
|---|---|---|---|---|---|
| Qwen3-VL-2B Instruct | 2B, Ollama download 1.9 GB | Apache-2.0 (HF card) | transformers (card says install from source), Ollama needs 0.12.7 | DocVQA test 93.3, InfoVQA test 72.4, OCRBench 858 (technical report Table 4) | none |
| Qwen3-VL-4B Instruct | 4B, Ollama 3.3 GB | not read on its card; same family, Apache-2.0 unverified | same | DocVQA test 95.3, InfoVQA 80.3, OCRBench 881 | none |
| PaddleOCR-VL-1.5 | 0.9B (Hub shows 1.0B) | Apache-2.0 | transformers 5.0 or later, vLLM, llama.cpp (GGUF linked in card news), Ollama only via community quantizations | OmniDocBench v1.5 94.5 overall; Real5-OmniDocBench 92.05; no receipt or invoice benchmark | A100 chart only (image), no CPU figure |
| SmolDocling-256M-preview | 0.3B | tag says cdla-permissive-2.0, summary says Apache-2.0; the card contradicts itself | transformers, vLLM, ONNX, MLX | none on card; successor granite-docling-258M is the supported line | "Avg of 0.35 secs per page on A100 GPU" (GPU, not CPU) |
| SmolVLM2-500M | 0.5B | Apache-2.0 | transformers; llama.cpp GGUF exists for SmolVLM2-256M and 500M | none for documents on the card (only video benchmarks) | none |
| Donut (CORD fine-tune) | about 143M excluding vocabulary | MIT | transformers | Paper Table 2 on CORD: field F1 84.1, tree accuracy 90.9; 91.1 accuracy at 1280x960 | 1.2 s per image (0.7 s at the paper's 1280x960 setting), measured on a P40 GPU, not CPU |
| docTR (OCR only, plus KIE predictor) | small CNN and transformer detectors | Apache-2.0 | PyTorch, ONNX | none on its README | none |

Sources, all read 2026-10-22:
- Qwen3-VL-2B card https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct ; technical report https://arxiv.org/abs/2511.21631 (Table 4, columns for 2B and 4B instruct, DocVQA, InfoVQA, OCRBench rows); Ollama https://ollama.com/library/qwen3-vl.
- PaddleOCR-VL-1.5 https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5.
- SmolDocling https://huggingface.co/docling-project/SmolDocling-256M-preview.
- SmolVLM2-500M https://huggingface.co/HuggingFaceTB/SmolVLM2-500M-Video-Instruct ; llama.cpp support list https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md.
- Donut card https://huggingface.co/naver-clova-ix/donut-base-finetuned-cord-v2 ; paper https://arxiv.org/abs/2111.15664 (Table 2 and the sentence on 1280x960).
- docTR https://github.com/mindee/doctr.

### Reading the table

- DocVQA, InfoVQA and OCRBench are general document question answering and text-reading scores. They are not field extraction on receipts. They say a model reads dense document text well, nothing about the DGII-shaped fields docmatch needs.
- The only receipt-specific number found is Donut's on CORD (Korean receipts, 2021). It is the sole model here whose accuracy is on a receipt benchmark, and it is old.
- Qwen3-VL on llama.cpp: the llama.cpp multimodal page as summarized by the fetcher did not list Qwen3-VL. That may be a truncated read. Ollama lists it, so Ollama is the confirmed local route. Unverified for llama.cpp.
- Florence-2 was a named candidate in the ticket brief. I did not read its card in this pass: unverified, not assessed.
- With 4 GiB of VRAM, a 2B BF16 model (about 4 GB of weights) will not fit on the GPU unquantized; a 4-bit GGUF in Ollama (1.9 GB for 2B, 3.3 GB for 4B) would. That is arithmetic from the published download sizes, not a measured result.

## Open for the privacy grilling (#212)

1. Is `store=false` (Gemini, OpenAI) acceptable, or is any abuse log, 55 days at Google and 30 at OpenAI, already a no?
2. Azure's 24-hour retention and in-region processing are the shortest hosted terms found, but its training-use statement is not on a primary page read. Reading the Products and Services DPA would settle it.
3. The only route to documented zero retention is OpenAI ZDR by approval, or Vertex AI for Gemini. Both need an account change.
4. A local route needs a measured run on public sample receipts, never on private ones, to know latency and field accuracy here.
