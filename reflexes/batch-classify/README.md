# Reflex: Batch Classify

Classify a CSV of texts and write the labels back out to a new CSV. Runs 10 requests at a time.

## Setup

```bash
npm install
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx batch.ts input.csv
```

The input CSV needs a `text` column. Results land in `input-classified.csv` with `text,label,confidence`.

Defaults to the built-in `user-frustrated` reflex. Override with `REFLEX_MODEL_ID`.

## Example

`input.csv`:

```csv
id,text
1,"this is the third time I have asked"
2,"How do I reset my password?"
```

Output:

```
Classifying 2 texts with concurrency 10...
Classified 2/2
Results written to input-classified.csv
```

```csv
text,label,confidence
"this is the third time I have asked",Frustrated,0.9999
"How do I reset my password?",Not Frustrated,1.0000
```

The CSV parser here splits on commas — it does not handle commas inside quoted fields. Swap in a real CSV library if your data has them.

## What it does

1. Reads the CSV and finds the `text` column by header name
2. Classifies in slices of `CONCURRENCY` (10) with `Promise.all`, printing progress per slice
3. Takes the highest-scoring class per row
4. Writes `text,label,confidence` to `<input>-classified.csv`

Raise `CONCURRENCY` for larger files — reflex inference is ~15ms, so throughput is bound by how many requests you keep in flight.
