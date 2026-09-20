# Local open-weight models for the vector arm and the rerank arm

Research ticket #112. Feeds the Phase 3 map (#111), whose retrieval benchmark has four arms: trigram, vector, hybrid, hybrid plus rerank. This file records which small open-weight models could serve the vector arm (sentence embeddings) and the rerank arm (cross-encoder) on CPU in Python, with what each source states. It does not run any model and does not pick the arms' final models; the benchmark does that.

All pages were read on 2026-09-20. Hugging Face model facts (dimension, sequence length, file sizes, license, commit) come from the model repo's own files at the commit named in each entry, fetched through the Hugging Face raw-file and API endpoints. "Unverified" marks a claim the primary source did not confirm in the pages read. Nothing under `data/` or from DocILE was used.

## Summary

- **Embedding candidates:** `sentence-transformers/all-MiniLM-L6-v2` (6 layers, 22.7M parameters, 384 dimensions), `BAAI/bge-small-en-v1.5` and `intfloat/e5-small-v2` (12 layers, 33.4M parameters, 384 dimensions each), and `sentence-transformers/paraphrase-MiniLM-L3-v2` (3 layers, 17.4M parameters, 384 dimensions).
- **Reranker candidates:** `cross-encoder/ms-marco-MiniLM-L6-v2` (22.7M parameters), `cross-encoder/ms-marco-TinyBERT-L2-v2` (4.4M parameters), `BAAI/bge-reranker-base` (278M parameters).
- **No source publishes a CPU latency figure for any candidate on short text.** The cross-encoder cards publish documents per second on a V100 GPU. The sentence-transformers efficiency page benchmarks all-MiniLM-L6-v2 on an i7-13700K but only as relative-speedup charts. Latency has to be measured in the Phase 3 benchmark, not quoted.
- **Loading library:** `sentence-transformers` 6.1.0 loads every candidate but one (see the loading section for the caveat on `BAAI/bge-reranker-base`). Every model can be pinned to a commit with `revision=`.
- **Recommendation:** all-MiniLM-L6-v2 for the vector arm and ms-marco-MiniLM-L6-v2 for the rerank arm, with bge-small-en-v1.5 and ms-marco-TinyBERT-L2-v2 as the challengers to try if the first pair loses on quality or latency.

---

## 1. Candidate embedding models

Common to all four: they output one dense vector per input text, are loaded with `SentenceTransformer(...)`, and are English. The dimension comes from `word_embedding_dimension` in each repo's `1_Pooling/config.json`, the sequence limit from `max_seq_length` in `sentence_bert_config.json`, the layer count from `config.json`, and sizes from the Hugging Face file tree, all at the commit shown.

### 1.1 `sentence-transformers/all-MiniLM-L6-v2`

| Field | Value | Source |
|---|---|---|
| Model id | `sentence-transformers/all-MiniLM-L6-v2` | [model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2), read 2026-09-20 |
| Commit read | `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` (repo last modified 2026-06-01) | [HF API](https://huggingface.co/api/models/sentence-transformers/all-MiniLM-L6-v2), read 2026-09-20 |
| Embedding dimension | 384 (mean pooling) | [`1_Pooling/config.json`](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/raw/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/1_Pooling/config.json) at the commit above; the [card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) states 384 too |
| Parameters, layers | 22,713,728 (F32 and I64 buffers combined); 6 layers, hidden size 384 | [HF API](https://huggingface.co/api/models/sentence-transformers/all-MiniLM-L6-v2) `safetensors.total`; [`config.json`](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/raw/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/config.json) |
| Size on disk | `model.safetensors` 90.9 MB; `onnx/model.onnx` 90.4 MB; int8 ONNX (`onnx/model_qint8_avx512_vnni.onnx`, `onnx/model_quint8_avx2.onnx`) 23.0 MB each | [HF tree API](https://huggingface.co/api/models/sentence-transformers/all-MiniLM-L6-v2/tree/1110a243fdf4706b3f48f1d95db1a4f5529b4d41?recursive=true), read 2026-09-20. The card itself states no size; these are the repo's file sizes (decimal MB) |
| License | Apache-2.0 | HF API `cardData.license` and the card's front matter, read 2026-09-20 |
| Max sequence length | 256 word pieces (`max_seq_length` 256); the card says longer input is truncated. The underlying BERT config allows 512 positions | [`sentence_bert_config.json`](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/raw/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/sentence_bert_config.json), [card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2), "Intended uses" |
| Published throughput | **None on the model card.** The sentence-transformers [efficiency page](https://sbert.net/docs/sentence_transformer/usage/efficiency.html) (read 2026-09-20) includes this model (22.7M parameters) in a backend benchmark on a Core i7-13700K CPU (8 and 20 threads tried), with short-sentence inputs averaging 38.9 characters (stsb). It reports relative speedups against PyTorch FP32 as charts, not sentences per second. Absolute CPU throughput for this model: unverified |
| Intended use | The card says it is "intended to be used as a sentence and short paragraph encoder". Trained on a 1B sentence-pair dataset | [card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2), read 2026-09-20 |
| Input prefix | None required | card usage example, read 2026-09-20 |

### 1.2 `BAAI/bge-small-en-v1.5`

| Field | Value | Source |
|---|---|---|
| Model id | `BAAI/bge-small-en-v1.5` | [model card](https://huggingface.co/BAAI/bge-small-en-v1.5), read 2026-09-20 |
| Commit read | `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` (repo last modified 2024-02-22) | [HF API](https://huggingface.co/api/models/BAAI/bge-small-en-v1.5), read 2026-09-20 |
| Embedding dimension | 384 (CLS-token pooling) | [`1_Pooling/config.json`](https://huggingface.co/BAAI/bge-small-en-v1.5/raw/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a/1_Pooling/config.json) at the commit above |
| Parameters, layers | 33,360,512 (F32 and I64 buffers combined); 12 layers, hidden size 384 | [HF API](https://huggingface.co/api/models/BAAI/bge-small-en-v1.5) `safetensors.total`; [`config.json`](https://huggingface.co/BAAI/bge-small-en-v1.5/raw/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a/config.json) |
| Size on disk | `model.safetensors` 133.5 MB; `onnx/model.onnx` 133.1 MB. No int8 ONNX file in the repo | [HF tree API](https://huggingface.co/api/models/BAAI/bge-small-en-v1.5/tree/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a?recursive=true), read 2026-09-20. Card states no size |
| License | MIT | HF API `cardData.license`, card front matter, read 2026-09-20 |
| Max sequence length | 512 (`max_seq_length` 512, lowercasing on) | [`sentence_bert_config.json`](https://huggingface.co/BAAI/bge-small-en-v1.5/raw/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a/sentence_bert_config.json) |
| Published throughput | **None on the card** (only a remark that `use_fp16=True` in FlagEmbedding "speeds up computation with a slight performance degradation", which is a GPU option). Unverified for CPU |
| Input prefix | Optional. The card says the v1.5 models were improved "to enhance its retrieval ability without instruction", and that "you can generate embedding without instruction in all cases for convenience". It recommends the `Represent this sentence for searching relevant passages: ` instruction only for short-query-to-long-passage retrieval, and says to choose whichever setting scores better on your task | [card](https://huggingface.co/BAAI/bge-small-en-v1.5), FAQ, read 2026-09-20 |

### 1.3 `intfloat/e5-small-v2`

| Field | Value | Source |
|---|---|---|
| Model id | `intfloat/e5-small-v2` | [model card](https://huggingface.co/intfloat/e5-small-v2), read 2026-09-20 |
| Commit read | `ffb93f3bd4047442299a41ebb6fa998a38507c52` (repo last modified 2025-02-17) | [HF API](https://huggingface.co/api/models/intfloat/e5-small-v2), read 2026-09-20 |
| Embedding dimension | 384 (mean pooling) | [`1_Pooling/config.json`](https://huggingface.co/intfloat/e5-small-v2/raw/ffb93f3bd4047442299a41ebb6fa998a38507c52/1_Pooling/config.json) at the commit above |
| Parameters, layers | 33,360,512 (F32 and I64 buffers combined); 12 layers, hidden size 384 | [HF API](https://huggingface.co/api/models/intfloat/e5-small-v2) `safetensors.total`; [`config.json`](https://huggingface.co/intfloat/e5-small-v2/raw/ffb93f3bd4047442299a41ebb6fa998a38507c52/config.json) |
| Size on disk | `model.safetensors` 133.5 MB; int8 ONNX (`onnx/model_qint8_avx512_vnni.onnx`) 34.1 MB | [HF tree API](https://huggingface.co/api/models/intfloat/e5-small-v2/tree/ffb93f3bd4047442299a41ebb6fa998a38507c52?recursive=true), read 2026-09-20. Card states no size |
| License | MIT | HF API `cardData.license`, card front matter, read 2026-09-20 |
| Max sequence length | 512 (`max_seq_length` 512). The card says: "Long texts will be truncated to at most 512 tokens" and "This model only works for English texts" | [`sentence_bert_config.json`](https://huggingface.co/intfloat/e5-small-v2/raw/ffb93f3bd4047442299a41ebb6fa998a38507c52/sentence_bert_config.json), [card](https://huggingface.co/intfloat/e5-small-v2) |
| Published throughput | **None on the card.** Unverified |
| Input prefix | **Required.** The card says each input "should start with 'query: ' or 'passage: '". For symmetric tasks such as paraphrase retrieval it says to use the `query: ` prefix on both sides | [card](https://huggingface.co/intfloat/e5-small-v2), FAQ, read 2026-09-20 |

### 1.4 `sentence-transformers/paraphrase-MiniLM-L3-v2`

| Field | Value | Source |
|---|---|---|
| Model id | `sentence-transformers/paraphrase-MiniLM-L3-v2` | [model card](https://huggingface.co/sentence-transformers/paraphrase-MiniLM-L3-v2), read 2026-09-20 |
| Commit read | `4ca70771034acceecb2e72475f72050fcdde4ddc` (repo last modified 2025-03-06) | [HF API](https://huggingface.co/api/models/sentence-transformers/paraphrase-MiniLM-L3-v2), read 2026-09-20 |
| Embedding dimension | 384 (mean pooling) | [`1_Pooling/config.json`](https://huggingface.co/sentence-transformers/paraphrase-MiniLM-L3-v2/raw/4ca70771034acceecb2e72475f72050fcdde4ddc/1_Pooling/config.json); the [card](https://huggingface.co/sentence-transformers/paraphrase-MiniLM-L3-v2) states 384 too |
| Parameters, layers | 17,390,336 (F32 and I64 buffers combined); 3 layers, hidden size 384 | [HF API](https://huggingface.co/api/models/sentence-transformers/paraphrase-MiniLM-L3-v2) `safetensors.total`; [`config.json`](https://huggingface.co/sentence-transformers/paraphrase-MiniLM-L3-v2/raw/4ca70771034acceecb2e72475f72050fcdde4ddc/config.json) |
| Size on disk | `model.safetensors` 69.6 MB; int8 ONNX 17.5 MB | [HF tree API](https://huggingface.co/api/models/sentence-transformers/paraphrase-MiniLM-L3-v2/tree/4ca70771034acceecb2e72475f72050fcdde4ddc?recursive=true), read 2026-09-20. Card states no size |
| License | Apache-2.0 | HF API `cardData.license`, card front matter, read 2026-09-20 |
| Max sequence length | 128 (`max_seq_length` 128) | [`sentence_bert_config.json`](https://huggingface.co/sentence-transformers/paraphrase-MiniLM-L3-v2/raw/4ca70771034acceecb2e72475f72050fcdde4ddc/sentence_bert_config.json) |
| Published throughput | **None on the card.** Unverified |
| Input prefix | None required |
| Training data | Paraphrase and question-pair style sets (SNLI, MultiNLI, QQP, Yahoo Answers, MS MARCO among others), per the card's dataset list. It is a paraphrase model, not a retrieval model | [card](https://huggingface.co/sentence-transformers/paraphrase-MiniLM-L3-v2) front matter |

### 1.5 What the text-length constraint means for these limits

The corpus's distinct normalized descriptions have p50 21 characters and p95 83 characters (ticket #112). Every candidate's limit (128 to 512 word pieces) is far above that, so truncation is not a factor and the 512 limits of bge-small and e5-small buy nothing. Only layer count and hidden size drive per-query cost here; the layer counts are in the tables above. That cost ordering (3, 6, 12, 12 layers, all at hidden size 384) is read off `config.json`. Actual CPU latency is **unverified** for all four, since none of the sources publishes it.

---

## 2. Candidate cross-encoder rerankers

A cross-encoder takes a pair of texts (query, candidate) and outputs a **relevance score**, not an embedding. An embedding dimension does not apply. The sentence-transformers docs say: "Cross-Encoders do not work for individual sentences and they don't compute embeddings for individual texts" ([pretrained models page](https://sbert.net/docs/cross_encoder/pretrained_models.html), read 2026-09-20).

### 2.1 `cross-encoder/ms-marco-MiniLM-L6-v2`

| Field | Value | Source |
|---|---|---|
| Model id | `cross-encoder/ms-marco-MiniLM-L6-v2` | [model card](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2), read 2026-09-20 |
| Commit read | `233902d25c440f23af6f7d6e94d2946bac0bee0a` (repo last modified 2026-08-09) | [HF API](https://huggingface.co/api/models/cross-encoder/ms-marco-MiniLM-L6-v2), read 2026-09-20 |
| Output | One relevance score per (query, passage) pair (`BertForSequenceClassification`, one label). The card's sentence-transformers example prints `[ 8.607138 -4.320078]`, which are raw logits. The current `CrossEncoder` docs say the default activation is `nn.Sigmoid()` when `num_labels=1`, so the scores your installed version prints may be sigmoid-squashed instead. Which one this model prints under 6.1.0 is unverified; the ranking order is the same either way | [card](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2), [`CrossEncoder` reference](https://sbert.net/docs/package_reference/cross_encoder/cross_encoder.html), read 2026-09-20 |
| Parameters, layers | 22,714,113; 6 layers, hidden size 384 | [HF API](https://huggingface.co/api/models/cross-encoder/ms-marco-MiniLM-L6-v2) `safetensors.total`; `config.json` |
| Size on disk | `model.safetensors` 90.9 MB; `onnx/model.onnx` 91.0 MB; int8 ONNX 23.2 MB | [HF tree API](https://huggingface.co/api/models/cross-encoder/ms-marco-MiniLM-L6-v2/tree/233902d25c440f23af6f7d6e94d2946bac0bee0a?recursive=true), read 2026-09-20 |
| License | Apache-2.0 | HF API, card front matter |
| Max sequence length | The model config allows 512 positions. `CrossEncoder(max_length=None)` uses the model's maximum, per the [reference](https://sbert.net/docs/package_reference/cross_encoder/cross_encoder.html). The card's own example passes no explicit length | `config.json`, sbert reference |
| Published throughput | Card table: **1800 docs per second, measured on a V100 GPU** ("Runtime was computed on a V100 GPU"). No CPU figure. Passages are MS MARCO paragraphs, so short catalog strings will not match that number | [card](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2), read 2026-09-20 |
| Published quality | NDCG@10 74.30 (TREC DL 19), MRR@10 39.01 (MS MARCO dev). Both are for question-to-paragraph passage ranking, not line-item descriptions | same card |
| Training task | MS MARCO passage ranking: a query is scored against a paragraph | same card |

### 2.2 `cross-encoder/ms-marco-TinyBERT-L2-v2`

| Field | Value | Source |
|---|---|---|
| Model id | `cross-encoder/ms-marco-TinyBERT-L2-v2` | [model card](https://huggingface.co/cross-encoder/ms-marco-TinyBERT-L2-v2), read 2026-09-20 |
| Commit read | `81d1926f67cb8eee2c2be17ca9f793c7c3bd20cc` (repo last modified 2025-08-29) | [HF API](https://huggingface.co/api/models/cross-encoder/ms-marco-TinyBERT-L2-v2), read 2026-09-20 |
| Output | One relevance score per pair (`BertForSequenceClassification`); same score-versus-sigmoid caveat as 2.1 | card, `config.json` |
| Parameters, layers | 4,386,561; 2 layers, hidden size 128 | [HF API](https://huggingface.co/api/models/cross-encoder/ms-marco-TinyBERT-L2-v2) `safetensors.total`; `config.json` |
| Size on disk | `model.safetensors` 17.6 MB; int8 ONNX 4.5 MB | [HF tree API](https://huggingface.co/api/models/cross-encoder/ms-marco-TinyBERT-L2-v2/tree/81d1926f67cb8eee2c2be17ca9f793c7c3bd20cc?recursive=true), read 2026-09-20 |
| License | Apache-2.0 | HF API, card front matter |
| Max sequence length | The model config allows 512 positions; the card's sentence-transformers example passes `max_length=512` | `config.json`, [card](https://huggingface.co/cross-encoder/ms-marco-TinyBERT-L2-v2) |
| Published throughput | Card table: **9000 docs per second on a V100 GPU**, the fastest row in the table. No CPU figure | [card](https://huggingface.co/cross-encoder/ms-marco-TinyBERT-L2-v2), read 2026-09-20 |
| Published quality | NDCG@10 69.84, MRR@10 32.56 (same MS MARCO caveat) | same card |

### 2.3 `BAAI/bge-reranker-base`

| Field | Value | Source |
|---|---|---|
| Model id | `BAAI/bge-reranker-base` | [model card](https://huggingface.co/BAAI/bge-reranker-base), read 2026-09-20 |
| Commit read | `2cfc18c9415c912f9d8155881c133215df768a70` (repo last modified 2024-06-24) | [HF API](https://huggingface.co/api/models/BAAI/bge-reranker-base), read 2026-09-20 |
| Output | A relevance score per (question, document) pair. The card says the reranker "directly output[s] similarity instead of embedding" and that "the relevance score is not bounded to a specific range" (trained with cross-entropy loss) | [card](https://huggingface.co/BAAI/bge-reranker-base), "Usage for Reranker" |
| Parameters, layers | 278,044,931; 12 layers, hidden size 768, architecture `XLMRobertaForSequenceClassification` | [HF API](https://huggingface.co/api/models/BAAI/bge-reranker-base) `safetensors.total`; `config.json` |
| Size on disk | `model.safetensors` 1,112.2 MB; `onnx/model.onnx` 1,112.5 MB. No quantized file in the repo | [HF tree API](https://huggingface.co/api/models/BAAI/bge-reranker-base/tree/2cfc18c9415c912f9d8155881c133215df768a70?recursive=true), read 2026-09-20 |
| License | MIT | HF API, card front matter |
| Max sequence length | The card's transformers example uses `max_length=512`; the config allows 514 positions | [card](https://huggingface.co/BAAI/bge-reranker-base), `config.json` |
| Languages | English and Chinese per the card's language tags and model list | card front matter |
| Published throughput | **None on the card.** Unverified |

---

## 3. Loading library and current version

| Item | Value | Source |
|---|---|---|
| Package | `sentence-transformers` | [PyPI](https://pypi.org/project/sentence-transformers/) |
| Current version | **6.1.0**, uploaded 2026-09-18, requires Python 3.10 or newer | [PyPI JSON](https://pypi.org/pypi/sentence-transformers/json), read 2026-09-20 |
| Dependencies that matter | `torch>=2.2`, `transformers>=5.0.0,<6.0.0`, `huggingface-hub>=1.3.0,<2.0.0`, `numpy>=1.24.0`, `scikit-learn`, `scipy` | same PyPI JSON, `requires_dist` |
| `huggingface-hub` current version | 1.32.0, uploaded 2026-09-17 | [PyPI JSON](https://pypi.org/pypi/huggingface-hub/json), read 2026-09-20 |
| Add to `engine/pyproject.toml` | `sentence-transformers==6.1.0` (no other package needed for the seven models in a sentence-transformers or transformers loading path) | this file's recommendation |

Which class loads which candidate:

| Candidate | Loader | Basis |
|---|---|---|
| all-MiniLM-L6-v2, bge-small-en-v1.5, e5-small-v2, paraphrase-MiniLM-L3-v2 | `SentenceTransformer(...)` | Each repo ships `modules.json`, `1_Pooling/config.json` and `sentence_bert_config.json` (HF tree API listing, read 2026-09-20), the layout sentence-transformers reads |
| ms-marco-MiniLM-L6-v2 | `CrossEncoder(...)` | The card's own example imports `from sentence_transformers import CrossEncoder` |
| ms-marco-TinyBERT-L2-v2 | `CrossEncoder(...)` | Same, in the card's sentence-transformers example |
| bge-reranker-base | `CrossEncoder(...)` **unverified** | The card documents only `FlagEmbedding` and plain `transformers` loading, not sentence-transformers. It is a standard sequence-classification model, which `CrossEncoder` is documented to wrap, but this file did not verify that it loads correctly there. The transformers path (`AutoModelForSequenceClassification`) is documented on the card |

CPU torch: the [uv PyTorch guide](https://docs.astral.sh/uv/guides/integration/pytorch/) (read 2026-09-20) documents adding a `[[tool.uv.index]]` entry for `https://download.pytorch.org/whl/cpu` to use CPU-only builds. Whether the default PyPI `torch` wheel on Linux is heavier than the CPU index build was not verified here.

Backend options that could lower CPU latency: the [efficiency page](https://sbert.net/docs/sentence_transformer/usage/efficiency.html) documents `backend="onnx"` and `backend="openvino"` (extras `onnx` and `openvino`), and int8 quantized ONNX files already ship in the MiniLM repos above. Speedups for these specific models on short text: unverified. That page's only CPU benchmark hardware is an i7-13700K, and its figures are relative to PyTorch FP32, not absolute.

---

## 4. Revision pinning

**Documented.** The `SentenceTransformer` and `CrossEncoder` constructors both take `revision`: "The specific model version to use. It can be a branch name, a tag name, or a commit id, for a stored model on Hugging Face. Defaults to None." (sentence-transformers [`SentenceTransformer` reference](https://sbert.net/docs/package_reference/sentence_transformer/SentenceTransformer.html) and [`CrossEncoder` reference](https://sbert.net/docs/package_reference/cross_encoder/cross_encoder.html), read 2026-09-20, for the docs version that matches 6.1.0 on PyPI: the docs page lists no version string, so the exact match is unverified.) The underlying `huggingface_hub` documents the same parameter for `hf_hub_download`: "use the revision parameter" with a tag, branch, PR or commit hash ([download guide](https://huggingface.co/docs/huggingface_hub/en/guides/download), read 2026-09-20).

Exact syntax:

```python
from sentence_transformers import CrossEncoder, SentenceTransformer

embedder = SentenceTransformer(
    "sentence-transformers/all-MiniLM-L6-v2",
    revision="1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
)
reranker = CrossEncoder(
    "cross-encoder/ms-marco-MiniLM-L6-v2",
    revision="233902d25c440f23af6f7d6e94d2946bac0bee0a",
)
```

`local_files_only=True` (a documented constructor argument on both classes) then keeps a run fully offline after the first download.

Commits read today, read from each repo's HF API record (`sha`) on 2026-09-20 ([API endpoint pattern](https://huggingface.co/api/models/sentence-transformers/all-MiniLM-L6-v2)):

| Model | Commit |
|---|---|
| `sentence-transformers/all-MiniLM-L6-v2` | `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` |
| `BAAI/bge-small-en-v1.5` | `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` |
| `intfloat/e5-small-v2` | `ffb93f3bd4047442299a41ebb6fa998a38507c52` |
| `sentence-transformers/paraphrase-MiniLM-L3-v2` | `4ca70771034acceecb2e72475f72050fcdde4ddc` |
| `cross-encoder/ms-marco-MiniLM-L6-v2` | `233902d25c440f23af6f7d6e94d2946bac0bee0a` |
| `cross-encoder/ms-marco-TinyBERT-L2-v2` | `81d1926f67cb8eee2c2be17ca9f793c7c3bd20cc` |
| `BAAI/bge-reranker-base` | `2cfc18c9415c912f9d8155881c133215df768a70` |

Repos do move: the all-MiniLM-L6-v2 and cross-encoder/ms-marco-MiniLM-L6-v2 repos show `lastModified` dates in 2026 (HF API, read 2026-09-20), so an unpinned load can change under a benchmark row. **How to freeze:** put the model id and full 40-character commit in code as constants, pass them as `revision=`, and record the commit and the `sentence-transformers` version beside every result row, the same way the benchmark records other versions. Use the commit, never a branch name, since `main` moves. Whether the repos' `main` commits are the ones a maintainer intends as stable releases is unverified.

---

## 5. Recommendation

**Vector arm: `sentence-transformers/all-MiniLM-L6-v2`.** It is the smallest of the three retrieval-oriented candidates by a wide margin in the one number the sources give: 6 layers and 22.7M parameters against 12 layers and 33.4M for bge-small and e5-small (all at hidden size 384, so the same 384-dimension index). Its card describes it as a sentence and short paragraph encoder, which matches 21-character median inputs. It needs no prefix, so the query and catalog sides share one code path, and the repo ships a 23.0 MB int8 ONNX file as a later latency lever. The sentence-transformers efficiency page also uses it as its smallest benchmarked model, so it is the best-documented case for CPU speedups (relative figures only).

- **Against `BAAI/bge-small-en-v1.5`:** 12 layers roughly double the transformer depth for inputs this short, and its extra 512-token capacity is unused. The cost difference is inferred from layer counts, not measured. It is the right challenger if all-MiniLM-L6-v2 loses on top-1 or top-5, and its prefix is optional, so it costs no extra code.
- **Against `intfloat/e5-small-v2`:** same depth as bge-small, and the card requires `query: ` or `passage: ` prefixes on every input. That adds a way to get the arm silently wrong for no stated benefit on short symmetric phrases.
- **Against `paraphrase-MiniLM-L3-v2`:** cheapest by layer count (3), but trained on paraphrase and question-pair data with a 128-token limit, not for retrieval. Keep it in reserve as a latency floor to bracket the trade, not as the default.

**Rerank arm: `cross-encoder/ms-marco-MiniLM-L6-v2`.** It has the best published quality of the small options (NDCG@10 74.30) at the same 22.7M size as the recommended embedder, its card documents the `CrossEncoder` loading path directly, and an int8 ONNX file (23.2 MB) ships in the repo. Its known weakness is that it was trained on question-to-paragraph ranking, not description-to-catalog-entry ranking. No source here measures how it behaves on noisy line-item text, so that fit is an assumption the benchmark must test.

- **Against `cross-encoder/ms-marco-TinyBERT-L2-v2`:** 5 times smaller and the fastest row on the card (9000 docs per second on a V100 against 1800), but lower on both published quality numbers. It is the challenger if rerank latency, a README column, dominates the trade.
- **Against `BAAI/bge-reranker-base`:** 278M parameters and a 1.1 GB file, about 12 times the parameters of the MiniLM cross-encoder, with no published CPU figure and a bilingual English and Chinese design, none of which helps 21-character English inputs. Loading it through `CrossEncoder` is also unverified. Not a candidate for a CPU latency column.

Both recommended models are Apache-2.0, so no license question arises for local use. The choice above is a starting point drawn from documentation only. Per-query CPU latency and retrieval quality on the synthetic catalog are measured in the benchmark tickets, and either recommendation should be replaced by its challenger if those numbers say so.

---

## Unverified in this pass

- Absolute CPU latency or throughput for every candidate (no model card publishes it; sentence-transformers publishes all-MiniLM-L6-v2 CPU results only as relative speedup charts on an i7-13700K).
- Speedups from the ONNX, OpenVINO and int8 files for these specific models on short text.
- Retrieval quality of any candidate on noisy line-item descriptions; no source measures this. The MTEB tables inside the bge and e5 cards were not extracted.
- Whether `CrossEncoder` loads `BAAI/bge-reranker-base` (the card documents FlagEmbedding and transformers only).
- Whether the cross-encoder cards' printed scores are raw logits or sigmoid-activated under `sentence-transformers` 6.1.0.
- Whether the sentence-transformers docs pages read describe exactly release 6.1.0 (the pages show no version string).
- Whether the default PyPI `torch` wheel on Linux is heavier than the CPU-index build.
- Whether each repo's `main` commit is one its maintainers regard as stable.
