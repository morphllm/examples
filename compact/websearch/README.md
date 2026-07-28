# Compact: Web Search

Does compacting web pages before you feed them to an LLM cost you answer quality? This notebook measures it.

The pipeline: search with Perplexity, `curl` the result pages to get the raw HTML, compact that HTML with Morph at `compression_ratio: 0.2`, then ask GPT the same 10 questions twice — once over the raw pages, once over the compacted pages — and have a judge model compare the two sets of answers.

## Setup

```bash
pip install -r requirements.txt
```

Three keys are read from the environment:

```bash
export MORPH_API_KEY=your-morph-key
export PERPLEXITY_API_KEY=your-perplexity-key
export OPENAI_API_KEY=your-openai-key
```

The Perplexity key is required — the notebook has no offline mode.

## Run

```bash
jupyter notebook perplexity.ipynb
```

Run the cells top to bottom. Change `SEARCH_STRING` in cell 3 to search for something else.

## Why raw HTML

Compaction deletes whole lines and never rewrites them, so raw HTML is close to the ideal input: markup, nav, and script tags are dense, line-separated, and irrelevant to the query. The one thing to avoid is minified assets — if most of your payload is on a single line there is nothing for the model to cut.
