#!/usr/bin/env node
/**
 * sources/backfill-english-fields.ts
 *
 * One-time backfill: for all sources, generate:
 *   - name_en  (official English name or translation) — if missing/null
 *   - focus_en (English focus topics) — always
 *
 * Also fixes legacy entity_nature values:
 *   - "Research Institution/Group" → "Research Institution/Team"
 *   - "个人" → "KOL"
 *
 * Uses the Gemini enrichment pipeline in batches of 10.
 */

import { sb } from '../lib/supabase.ts'
import { GoogleGenerativeAI, SchemaType } from '@google/generative-ai'
import * as dotenv from 'dotenv'
dotenv.config()

const BATCH_SIZE = 10

interface EnrichmentInput {
  id:           string
  name:         string
  name_en:      string | null
  profile:      string | null
  focus:        string | null
  entity_nature: string | null
}

interface EnrichmentOutput {
  name_en:  string
  focus_en: string
}

const SCHEMA = {
  type: SchemaType.ARRAY,
  items: {
    type: SchemaType.OBJECT,
    properties: {
      name_en:  { type: SchemaType.STRING },
      focus_en: { type: SchemaType.STRING },
    },
    required: ['name_en', 'focus_en'],
  },
}

const PROMPT_HEADER = `You are an expert analyst for SinoArk, a Chinese tech and business media intelligence platform.

For each WeChat public account, provide two English fields:

1. name_en — Official English name. Rules:
   - If the org has a well-known official English name (major companies, publishers, gov bodies, universities), use it exactly (e.g. "Tencent", "People's Daily", "Peking University", "MIIT")
   - Search your knowledge for official English branding; prefer the most widely used form
   - If no official English name exists, provide a clean English translation of the Chinese name
   - If name_en is already provided and looks correct, keep it as-is

2. focus_en — English translation of the Chinese focus topics, comma-separated.
   Same number of topics as the Chinese input. Use natural English terms.

Return ONLY a JSON array (one object per account in order). No additional text.`

async function batchEnrich(items: EnrichmentInput[]): Promise<Array<EnrichmentOutput | null>> {
  if (!process.env.GEMINI_API_KEY) throw new Error('GEMINI_API_KEY not set')

  const model = new GoogleGenerativeAI(process.env.GEMINI_API_KEY).getGenerativeModel({
    model: 'gemini-2.5-flash',
    generationConfig: {
      responseMimeType: 'application/json',
      responseSchema: SCHEMA as any,
      temperature: 0.2,
    },
  })

  const list = items.map((s, i) => {
    const lines = [`${i + 1}. Chinese name: "${s.name}"`]
    if (s.name_en) lines.push(`   Current name_en: "${s.name_en}" (keep if correct)`)
    if (s.profile) lines.push(`   Chinese profile: "${s.profile.substring(0, 200)}"`)
    if (s.focus)   lines.push(`   Chinese focus: "${s.focus}"`)
    return lines.join('\n')
  }).join('\n\n')

  const prompt = `${PROMPT_HEADER}\n\nEnrich these ${items.length} accounts:\n\n${list}`

  try {
    const result = await model.generateContent(prompt)
    const arr = JSON.parse(result.response.text()) as EnrichmentOutput[]
    return items.map((_, i) => arr[i] ?? null)
  } catch (e) {
    console.error('Gemini batch failed:', (e as Error).message)
    return items.map(() => null)
  }
}

async function main() {
  console.log('=== backfill-english-fields starting ===')

  // ── 1. Fix legacy entity_nature values directly ───────────
  const fixes = [
    { from: 'Research Institution/Group', to: 'Research Institution/Team' },
    { from: '个人', to: 'KOL' },
  ]
  for (const { from, to } of fixes) {
    const { count, error } = await sb()
      .from('sources')
      .update({ entity_nature: to })
      .eq('entity_nature', from)
      .select('*', { count: 'exact', head: true })
    if (error) console.error(`  Fix "${from}": ${error.message}`)
    else console.log(`  Fixed entity_nature "${from}" → "${to}" (${count ?? 0} rows)`)
  }

  // ── 2. Fetch all sources ───────────────────────────────────
  const { data: sources, error } = await sb()
    .from('sources')
    .select('id, name, name_en, profile, focus, entity_nature')
    .order('name')

  if (error) { console.error('Failed to fetch sources:', error.message); process.exit(1) }
  console.log(`\nFetched ${sources!.length} sources to enrich`)

  let updated = 0
  let failed = 0

  // ── 3. Batch process ───────────────────────────────────────
  for (let i = 0; i < sources!.length; i += BATCH_SIZE) {
    const batch = sources!.slice(i, i + BATCH_SIZE) as EnrichmentInput[]
    const batchNum = Math.floor(i / BATCH_SIZE) + 1
    const totalBatches = Math.ceil(sources!.length / BATCH_SIZE)
    console.log(`\nBatch ${batchNum}/${totalBatches}: ${batch.map(s => s.name).join(', ')}`)

    const results = await batchEnrich(batch)

    for (let j = 0; j < batch.length; j++) {
      const source = batch[j]
      const result = results[j]

      if (!result) {
        console.error(`  ✗ ${source.name}: Gemini returned null`)
        failed++
        continue
      }

      // Only update name_en if currently empty/null
      const updateData: Record<string, string> = { focus_en: result.focus_en }
      if (!source.name_en) updateData['name_en'] = result.name_en

      const { error: updateErr } = await sb()
        .from('sources')
        .update(updateData)
        .eq('id', source.id)

      if (updateErr) {
        console.error(`  ✗ ${source.name}: ${updateErr.message}`)
        failed++
      } else {
        const nameNote = !source.name_en ? ` → ${result.name_en}` : ''
        console.log(`  ✓ ${source.name}${nameNote} | focus_en: ${result.focus_en}`)
        updated++
      }
    }

    // Brief pause between batches to avoid rate limiting
    if (i + BATCH_SIZE < sources!.length) {
      await new Promise(r => setTimeout(r, 2000))
    }
  }

  console.log(`\n=== Done. Updated: ${updated}, Failed: ${failed} ===`)
}

main().catch(e => { console.error(e); process.exit(1) })
