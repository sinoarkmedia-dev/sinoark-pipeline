/**
 * lib/profile.ts — AI-powered source profiling, translation, and labeling
 *
 * Two-step profiling strategy:
 *   1. Qwen API: Chinese profiling (profile, focus in Chinese, about_tech, about_ai)
 *   2. Gemini Flash: English enrichment (profile_en, focus_en, name_en, entity_nature)
 *
 * entity_nature is determined by Gemini (not Qwen) because:
 *   - The taxonomy is English-defined with nuanced rules
 *   - Gemini has broader knowledge of Chinese companies/media to apply them correctly
 *   - Official English name (name_en) requires web-informed knowledge
 */

import { GoogleGenerativeAI, SchemaType } from '@google/generative-ai'
import axios from 'axios'
import * as dotenv from 'dotenv'
import { warn, log } from './utils.ts'
dotenv.config()

export interface SourceProfile {
  profile:       string    // 2-3 sentence description in Chinese
  profile_en:    string    // English translation of profile
  name_en:       string    // Official English name (or best translation)
  entity_nature: string    // see ENTITY_NATURE_OPTIONS below
  focus:         string    // comma-separated key topics in Chinese
  focus_en:      string    // comma-separated key topics in English
  about_tech:    boolean
  about_ai:      boolean
}

// ─── entity_nature taxonomy ───────────────────────────────────
//
// EXACTLY ONE of these values must be chosen:
//   "Media"                  — self-claims media, OR major/endorsed outlet, OR 10+ years old / subsidiary thereof
//   "KOL"                    — small media, influencer, not yet an established media org
//   "Company"                — commercial company; NOT an investment org
//   "Investment Institution" — VC, PE, angel fund, investment research; NOT a company
//   "Education/Training"     — runs real-world education or training programs/activities;
//                              accounts that only distribute related info → Media or KOL instead
//   "Research Institution/Team" — dedicated research body (can be private) with consistent high-quality output
//   "Govt/Public Institution"   — government body, state-owned entity, regulatory authority, public agency
//   "Industrial Association" — industry trade group or professional body; NOT Govt/public institution
//   "Forum/Event"            — conference, summit, expo, or event brand; NOT association nor Govt
// ─────────────────────────────────────────────────────────────


// ─── Qwen API for Chinese profiling ──────────────────────────

interface QwenChineseProfile {
  profile:    string      // Chinese description 2-3 sentences
  focus:      string      // Chinese comma-separated topics
  about_tech: boolean
  about_ai:   boolean
}

const QWEN_PROFILE_PROMPT = `你是SinoArk（一个科技商业媒体平台）的媒体智能分析师。
分析每个微信公众号，生成结构化的中文档案。规则：
- profile: 2-3句中文，描述该账号的关注领域、内容风格和受众定位
- focus: 3-5个关键话题，中文逗号分隔（例如：人工智能,芯片设计,创业投资）
- about_tech: 如果主要涉及科技话题，则为true
- about_ai: 如果显著涉及AI/LLM/大模型话题，则为true

仅返回如下JSON格式（不要包含entity_nature字段）：
{"profile": "...", "focus": "...", "about_tech": true/false, "about_ai": true/false}`

/**
 * Batch call Qwen for Chinese profiling (up to 10 sources per call).
 */
async function batchQwenProfile(
  sources: Array<{ mpName: string; mpIntro: string | null }>
): Promise<Array<QwenChineseProfile | null>> {
  const qwenKey = process.env.QWEN_API_KEY
  if (!qwenKey) {
    warn('QWEN_API_KEY not set — skipping Qwen batch profiling')
    return sources.map(() => null)
  }
  if (sources.length === 0) return []

  try {
    const accountsList = sources
      .map((s, i) => `${i + 1}. 账号名称：${s.mpName}\n   账号简介：${s.mpIntro ?? '(无)'}`)
      .join('\n\n')

    const prompt = `${QWEN_PROFILE_PROMPT}\n\n按顺序分析以下${sources.length}个账号，返回JSON数组格式的分析结果：\n\n${accountsList}\n\n返回一个JSON数组，每个元素包含profile、focus、about_tech、about_ai字段。`

    const response = await axios.post(
      'https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation',
      {
        model: 'qwen-plus',
        input: { messages: [{ role: 'user', content: prompt }] },
        parameters: { result_format: 'message' },
      },
      {
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${qwenKey}`,
        },
      },
    )

    const content = response.data?.output?.choices?.[0]?.message?.content
    if (!content) {
      warn('Qwen: empty batch response')
      return sources.map(() => null)
    }

    const jsonMatch = content.match(/\[[\s\S]*\]/)
    if (!jsonMatch) {
      warn('Qwen: no JSON array in batch response')
      return sources.map(() => null)
    }

    const profiles = JSON.parse(jsonMatch[0]) as QwenChineseProfile[]
    return sources.map((_, i) => profiles[i] ?? null)
  } catch (e) {
    warn(`Qwen batch profiling failed: ${(e as Error).message}`)
    return sources.map(() => null)
  }
}


// ─── Gemini API for English enrichment ───────────────────────

interface GeminiEnrichmentInput {
  mpName:   string
  mpIntro:  string | null
  profile:  string   // Chinese profile from Qwen
  focus:    string   // Chinese focus from Qwen
}

interface GeminiEnrichmentOutput {
  name_en:       string   // Official English name or best translation
  profile_en:    string   // English translation/adaptation of profile
  focus_en:      string   // English topics, comma-separated
  entity_nature: string   // from taxonomy above
}

const GEMINI_ENRICHMENT_SCHEMA = {
  type: SchemaType.OBJECT,
  properties: {
    name_en:       { type: SchemaType.STRING },
    profile_en:    { type: SchemaType.STRING },
    focus_en:      { type: SchemaType.STRING },
    entity_nature: { type: SchemaType.STRING },
  },
  required: ['name_en', 'profile_en', 'focus_en', 'entity_nature'],
}

const BATCH_ENRICHMENT_SCHEMA = {
  type: SchemaType.ARRAY,
  items: GEMINI_ENRICHMENT_SCHEMA,
}

const GEMINI_ENRICHMENT_PROMPT = `You are an expert analyst for SinoArk, a Chinese tech and business media intelligence platform.

For each WeChat public account, produce English enrichment based on the Chinese account info provided.

FIELDS:
1. name_en — Official English name. Rules:
   - If the account/org has a well-known official English name (major companies, gov bodies, universities, established media), use it exactly (e.g. "Tencent", "Baidu", "People's Daily", "Peking University", "MIIT")
   - Search your knowledge for official English branding; if uncertain between options, prefer the most widely used form
   - If no official English name exists, provide a clean English translation of the Chinese name

2. profile_en — Fluent English description (2-3 sentences). Faithfully convey the substance of the Chinese profile. Write for an English-speaking tech/business audience.

3. focus_en — English translation of the Chinese focus topics, comma-separated. Same number of topics as the Chinese input. Use natural English terms (e.g. "artificial intelligence, chip design, venture capital").

4. entity_nature — Classify the account into EXACTLY ONE of these options. Read the rules carefully:
   - "Media": account self-identifies as media, OR is a major outlet with wide public endorsement, OR the organization was established 10+ years ago (or is a subsidiary of such an organization). This is the most common category for large established publications and news outlets.
   - "KOL": smaller account, individual influencer, or emerging media that does not yet qualify as established Media
   - "Company": commercial company or business; NOT an investment/VC/fund org
   - "Investment Institution": venture capital, private equity, angel fund, investment research firm; NOT a general company
   - "Education/Training": organization with real-world education or training programs/activities (schools, courses, bootcamps). If an account merely distributes education-related content without running actual programs, classify as Media or KOL instead.
   - "Research Institution/Team": dedicated research body (can be private/commercial) producing consistent, high-quality research output; think-tanks, academic labs, research centers
   - "Govt/Public Institution": government body, state-owned entity, regulatory authority (e.g. MIIT, CAAC), or public agency
   - "Industrial Association": industry trade group, professional body, or industry alliance; NOT a Govt/public institution
   - "Forum/Event": conference, summit, expo, or recurring event brand (e.g. World AI Conference, China Internet Conference); NOT an association, NOT a Govt body

Return ONLY a JSON array (one object per account in input order). No additional text.`

/**
 * Batch call Gemini to enrich profiles with English fields and entity_nature.
 */
async function batchGeminiEnrichment(
  inputs: GeminiEnrichmentInput[]
): Promise<Array<GeminiEnrichmentOutput | null>> {
  if (!process.env.GEMINI_API_KEY) {
    warn('GEMINI_API_KEY not set — skipping Gemini enrichment')
    return inputs.map(() => null)
  }
  if (inputs.length === 0) return []

  try {
    const model = new GoogleGenerativeAI(process.env.GEMINI_API_KEY).getGenerativeModel({
      model: 'gemini-2.5-flash',
      generationConfig: {
        responseMimeType: 'application/json',
        responseSchema: BATCH_ENRICHMENT_SCHEMA as any,
        temperature: 0.2,
      },
    })

    const list = inputs
      .map((inp, i) =>
        `${i + 1}. Chinese name: "${inp.mpName}"\n   Intro: "${inp.mpIntro ?? '(none)'}"\n   Chinese profile: "${inp.profile}"\n   Chinese focus: "${inp.focus}"`
      )
      .join('\n\n')

    const prompt = `${GEMINI_ENRICHMENT_PROMPT}\n\nEnrich these ${inputs.length} accounts in order:\n\n${list}`

    const result = await model.generateContent(prompt)
    const arr = JSON.parse(result.response.text()) as GeminiEnrichmentOutput[]
    return inputs.map((_, i) => arr[i] ?? null)
  } catch (e) {
    warn(`Gemini batch enrichment failed: ${(e as Error).message}`)
    return inputs.map(() => null)
  }
}


// ─── Public API ───────────────────────────────────────────────

/**
 * Batch generate profiles for up to 10 sources.
 * Step 1: Qwen (Chinese profiling — profile, focus, about_tech, about_ai)
 * Step 2: Gemini (English enrichment — name_en, profile_en, focus_en, entity_nature)
 */
export async function batchGenerateProfiles(
  sources: Array<{ mpName: string; mpIntro: string | null }>
): Promise<Array<SourceProfile | null>> {
  if (sources.length === 0) return []

  const QWEN_BATCH = 10
  const qwenResults: Array<QwenChineseProfile | null> = []

  // Step 1: Batch Qwen profiling (Chinese)
  for (let i = 0; i < sources.length; i += QWEN_BATCH) {
    const batch = sources.slice(i, i + QWEN_BATCH)
    const batchResults = await batchQwenProfile(batch)
    qwenResults.push(...batchResults)
  }

  // Step 2: Batch Gemini enrichment (English) — only for sources where Qwen succeeded
  const geminiInputs: GeminiEnrichmentInput[] = []
  const geminiIndexMap: number[] = []  // maps gemini result index → source index

  for (let i = 0; i < qwenResults.length; i++) {
    const q = qwenResults[i]
    if (q) {
      geminiInputs.push({
        mpName:  sources[i].mpName,
        mpIntro: sources[i].mpIntro,
        profile: q.profile,
        focus:   q.focus,
      })
      geminiIndexMap.push(i)
    }
  }

  const GEMINI_BATCH = 10
  const geminiResults: Array<GeminiEnrichmentOutput | null> = []
  for (let i = 0; i < geminiInputs.length; i += GEMINI_BATCH) {
    const batch = geminiInputs.slice(i, i + GEMINI_BATCH)
    const batchResults = await batchGeminiEnrichment(batch)
    geminiResults.push(...batchResults)
  }

  // Merge results
  const results: Array<SourceProfile | null> = sources.map(() => null)
  for (let gi = 0; gi < geminiIndexMap.length; gi++) {
    const si = geminiIndexMap[gi]
    const qwen = qwenResults[si]!
    const gemini = geminiResults[gi]
    if (!gemini) continue

    results[si] = {
      profile:       qwen.profile,
      profile_en:    gemini.profile_en,
      name_en:       gemini.name_en,
      entity_nature: gemini.entity_nature,
      focus:         qwen.focus,
      focus_en:      gemini.focus_en,
      about_tech:    qwen.about_tech,
      about_ai:      qwen.about_ai,
    }
  }

  return results
}

/**
 * Generate profile for a single source.
 */
export async function generateSourceProfile(
  mpName: string,
  mpIntro: string | null,
): Promise<SourceProfile | null> {
  const results = await batchGenerateProfiles([{ mpName, mpIntro }])
  return results[0] ?? null
}


// ─── Article translation + labeling ──────────────────────────

export interface ArticleLabels {
  translated_title:   string
  translated_summary: string
  about_tech:         boolean
  about_ai:           boolean
  china_related:      boolean
  content_type:       string    // news | analysis | opinion | product | report | interview | other
  theme:              string    // 1-3 word English theme tag e.g. "LLM regulation"
  key_entities:       string[]  // up to 5 names (people, companies, products)
  keywords:           string[]  // up to 8 English keywords
  is_original:        boolean   // original content vs reposted/aggregated
}

const LABEL_SCHEMA = {
  type: SchemaType.OBJECT,
  properties: {
    translated_title:   { type: SchemaType.STRING },
    translated_summary: { type: SchemaType.STRING },
    about_tech:         { type: SchemaType.BOOLEAN },
    about_ai:           { type: SchemaType.BOOLEAN },
    china_related:      { type: SchemaType.BOOLEAN },
    content_type:       { type: SchemaType.STRING },
    theme:              { type: SchemaType.STRING },
    key_entities:       { type: SchemaType.ARRAY, items: { type: SchemaType.STRING } },
    keywords:           { type: SchemaType.ARRAY, items: { type: SchemaType.STRING } },
    is_original:        { type: SchemaType.BOOLEAN },
  },
  required: ['translated_title','translated_summary','about_tech','about_ai',
             'china_related','content_type','theme','key_entities','keywords','is_original'],
}

const BATCH_LABEL_SCHEMA = {
  type: SchemaType.ARRAY,
  items: LABEL_SCHEMA,
}

const LABEL_INSTRUCTIONS = `You are a Chinese tech/business media analyst for SinoArk.
For each article, translate title and summary to English and classify. Rules:
- translated_title: natural English translation
- translated_summary: fluent English summary (2-3 sentences), NOT a literal translation
- about_tech: true if the article is primarily about technology
- about_ai: true if AI/LLM is a significant focus
- china_related: true if the article is primarily about China or Chinese companies/policy
- content_type: one of: news, analysis, opinion, product, report, interview, other
- theme: 1-3 word English theme (e.g. "AI regulation", "chip supply chain", "LLM benchmark")
- key_entities: up to 5 important named entities (people, companies, products) in English
- keywords: up to 8 English keywords for search/classification
- is_original: true if this appears to be original reporting, false if it's a repost/aggregation`

/**
 * Batch translate and label articles. Processes up to 20 per API call.
 */
export async function batchLabelArticles(
  articles: Array<{ title: string; summary: string | null; sourceName: string }>
): Promise<Array<ArticleLabels | null>> {
  if (!process.env.GEMINI_API_KEY) {
    warn('GEMINI_API_KEY not set — skipping labeling')
    return articles.map(() => null)
  }
  if (articles.length === 0) return []

  const CHUNK = 20
  const results: Array<ArticleLabels | null> = []

  for (let i = 0; i < articles.length; i += CHUNK) {
    const chunk = articles.slice(i, i + CHUNK)
    try {
      const model = new GoogleGenerativeAI(process.env.GEMINI_API_KEY!).getGenerativeModel({
        model: 'gemini-2.5-flash',
        generationConfig: {
          responseMimeType: 'application/json',
          responseSchema: BATCH_LABEL_SCHEMA as any,
          temperature: 0.2,
        },
      })
      const list = chunk.map((a, j) =>
        `${j + 1}. source="${a.sourceName}" title="${a.title}" summary="${(a.summary ?? '').substring(0, 300)}"`
      ).join('\n')
      const prompt = `${LABEL_INSTRUCTIONS}\n\nLabel these ${chunk.length} articles in order:\n${list}`
      const result = await model.generateContent(prompt)
      const arr = JSON.parse(result.response.text()) as ArticleLabels[]
      results.push(...chunk.map((_, j) => arr[j] ?? null))
    } catch (e) {
      warn(`Batch label failed for chunk ${i}-${i + chunk.length}: ${(e as Error).message}`)
      results.push(...chunk.map(() => null))
    }
  }
  return results
}
