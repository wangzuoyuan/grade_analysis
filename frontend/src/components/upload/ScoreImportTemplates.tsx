'use client'

import { useState } from 'react'
import { Download } from 'lucide-react'
import { Button } from '@/components/ui/button'

type Template = { file: string; label: string; filename: string }

const legacyTemplates: Template[] = [
  { file: 'homeroom-g1-students', label: '高一全科成绩明细', filename: '高一全科成绩明细模板' },
  { file: 'homeroom-g1-averages', label: '高一班级均分', filename: '高一班级均分模板' },
  { file: 'homeroom-g23-students', label: '高二 / 高三全科成绩明细', filename: '高二高三全科成绩明细模板' },
  { file: 'homeroom-g23-averages', label: '高二 / 高三班级均分', filename: '高二高三班级均分模板' },
]

export function ScoreImportTemplates({ mode, grade = 2, legacy = false }: {
  mode: 'homeroom' | 'teaching'; grade?: number; legacy?: boolean
}) {
  const [downloading, setDownloading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const gradeName = grade === 1 ? '高一' : grade === 3 ? '高三' : '高二'
  const platform = mode === 'teaching' && !legacy
  const compatibleTemplates = mode === 'teaching' ? [
    ...legacyTemplates.filter(t => t.file.includes('-g1-')),
    ...['高二', '高三'].flatMap(g => legacyTemplates.filter(t => t.file.includes('-g23-')).map(t => ({
      ...t, label: t.label.replace('高二 / 高三', g), filename: t.filename.replace('高二高三', g),
    }))),
  ] : legacyTemplates
  const templates: Template[] = !platform ? compatibleTemplates : grade === 1 ? [
    { file: 'teaching-g1-students', label: '学生成绩明细', filename: `${gradeName}学生成绩明细模板` },
    { file: 'teaching-g1-quality', label: '成绩质量分析', filename: `${gradeName}成绩质量分析模板` },
  ] : [
    { file: 'teaching-g23-students', label: '学生成绩明细', filename: `${gradeName}学生成绩明细模板` },
    { file: 'teaching-g23-teaching-quality', label: '教学班质量分析', filename: `${gradeName}教学班成绩质量分析模板` },
    { file: 'teaching-g23-admin-quality', label: '行政班质量分析', filename: `${gradeName}行政班成绩质量分析模板` },
  ]
  async function download(template: Template) {
    if (downloading) return
    setDownloading(template.file)
    setError(null)
    try {
      const response = await fetch(`/templates/scores/v1/${template.file}.xlsx`)
      if (!response.ok) throw new Error('download failed')
      // Hosting may attach its internal filename in Content-Disposition. A blob
      // download preserves the grade-bearing Chinese name used by legacy imports.
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url
      link.download = `${template.filename}.xlsx`
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 30_000)
    } catch {
      setError('模板下载失败，请检查网络后重试。')
    } finally {
      setDownloading(null)
    }
  }
  return <section aria-label="导入模板下载" className="space-y-3 rounded-lg border border-sky-100 bg-sky-50/40 p-4 print:hidden">
    <div>
      <h2 className="text-sm font-medium text-slate-800">导入模板下载{platform ? ` · ${gradeName}` : mode === 'teaching' ? ' · 旧格式' : ' · 班主任'}</h2>
      <p className="mt-1 text-xs leading-5 text-slate-500">下载空白 Excel，保留表头和列顺序，从首个空白行开始填写。学号按文本填写，保留前导零。</p>
    </div>
    <div className="flex flex-wrap gap-2">
      {templates.map(t => <Button key={t.filename} asChild variant="outline" size="sm" className="h-auto max-w-full whitespace-normal py-2 text-left">
        <a href={`/templates/scores/v1/${t.file}.xlsx`} download={`${t.filename}.xlsx`}
          aria-disabled={downloading !== null} aria-busy={downloading === t.file}
          onClick={event => { event.preventDefault(); void download(t) }}>
          <Download className="mr-2 h-4 w-4 shrink-0" aria-hidden="true"/>{downloading === t.file ? '下载中…' : t.label}
        </a>
      </Button>)}
    </div>
    {error ? <p role="alert" className="text-xs text-red-700">{error}</p> : null}
    <p className="text-xs leading-5 text-slate-500">{platform
      ? grade === 1
        ? '学籍排名、年级百分位可留空后补。质量表填写本次实际班级，任课教师为空的真实班级仍纳入对比。'
        : '等级分可留空后补，补录沿用同一明细模板。教学班全部纳入对比，行政班仅纳入任课教师非空的班级。'
      : '班级填写行政班数字班号。上传时明确填写考试名称和日期；同场明细与均分表使用相同考试名称和日期。'}
      {mode === 'teaching' && legacy ? ' 旧格式只导入本人任教学科；单科质量分析请返回教学平台单科导入。' : null}
    </p>
    {platform ? <p className="text-xs leading-5 text-slate-500">比率和百分位按百分比填写，如 25%。应考、实考、均分、标准差及等级人数均按官方数据填写，未知项留空；不根据原始分推算等级分。</p> : null}
  </section>
}
