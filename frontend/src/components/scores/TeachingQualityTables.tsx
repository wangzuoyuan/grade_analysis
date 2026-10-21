import { Fragment } from 'react'
import type { PlatformCompare, PlatformMetrics } from '@/lib/api-v1'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

import { qualityNumber, qualityPercent } from '@/lib/teaching-score-display'

const gradeBands = ['A+', 'A', 'B+', 'B', 'B-', 'C+', 'C', 'C-', 'D+', 'D', 'E']
const averages: Array<[keyof PlatformMetrics, string]> = [
  ['chinese_avg', '语文'], ['math_avg', '数学'], ['english_avg', '英语'], ['main_three_avg', '三大门'],
]
const sourceLabels: Record<string, string> = { official: '官方', estimated: '学生样本估算', unavailable: '尚无统计' }
type ClassRow = PlatformCompare['classes'][number]
export const byClassName = (a: ClassRow, b: ClassRow) =>
  a.class_label.localeCompare(b.class_label, 'zh-CN', { numeric: true }) || a.id.localeCompare(b.id)

export function TeachingQualityTables({ data, grade }: { data: PlatformCompare; grade: number }) {
  return <>{(['administrative', 'teaching'] as const).map(kind => {
    // 原表区只展示已导入的官方指标，不以学生样本估算补齐原表空值。
    const rows = data.classes.filter(c => c.class_kind === kind && c.source === 'official').sort(byClassName)
    const bands = grade === 1 ? Array.from(new Set(rows.flatMap(c => (c.metrics.bands ?? []).map(b => b.label)))) : gradeBands
    return <Card key={kind} className="min-w-0">
      <CardHeader><CardTitle>{kind === 'administrative' ? '行政班质量分析' : '教学班质量分析'}</CardTitle>
        <p className="text-sm text-slate-500">{data.exam.subject}质量分析表 · {rows.length} 个班级 · 官方指标按导入文件展示，空值为“—”。</p>
      </CardHeader>
      <CardContent>{!rows.length ? <p className="py-6 text-sm text-slate-500">本场尚未导入{kind === 'administrative' ? '行政班' : '教学班'}质量表。</p> :
        <Table className="whitespace-nowrap" aria-label={`${kind === 'administrative' ? '行政班' : '教学班'}质量分析表`}>
          <TableHeader>
            <TableRow>
              <TableHead rowSpan={2}>班级</TableHead><TableHead rowSpan={2}>任课教师</TableHead>
              <TableHead colSpan={2} className="text-center">人数</TableHead>
              <TableHead colSpan={grade === 1 ? 1 : 2} className="text-center">平均分</TableHead>
              <TableHead rowSpan={2} className="text-right">标准差</TableHead>
              {(grade === 1 ? ['单科总分', '最高分', '最低分'] : averages.map(([, label]) => label)).map(label =>
                <TableHead key={label} rowSpan={2} className="text-right">{label}</TableHead>)}
              {bands.length > 0 && <TableHead colSpan={bands.length * 2} className="text-center">{grade === 1 ? '分数段人数及比率' : '等级人数及比率'}</TableHead>}
            </TableRow>
            <TableRow><TableHead className="text-right">应考</TableHead><TableHead className="text-right">实考</TableHead>
              <TableHead className="text-right">原始</TableHead>{grade !== 1 && <TableHead className="text-right">等级</TableHead>}
              {bands.map(label => <Fragment key={label}><TableHead className="text-right">{label}</TableHead><TableHead className="text-right">比率</TableHead></Fragment>)}
            </TableRow>
          </TableHeader>
          <TableBody>{rows.map(c => <TableRow key={c.id}>
            <TableCell className="font-medium">{c.class_label}</TableCell><TableCell>{c.teacher_name ?? '—'}</TableCell>
            <TableCell className="text-right tabular-nums">{qualityNumber(c.metrics.expected_count, 0)}</TableCell>
            <TableCell className="text-right tabular-nums">{qualityNumber(c.metrics.actual_count, 0)}</TableCell>
            <TableCell className="text-right tabular-nums">{qualityNumber(c.metrics.raw_avg)}</TableCell>
            {grade !== 1 && <TableCell className="text-right tabular-nums">{qualityNumber(c.metrics.grade_avg)}</TableCell>}
            <TableCell className="text-right tabular-nums" title={c.metrics.stddev_basis === 'unspecified' ? '官方未注明原始分/等级分口径' : '原始分标准差'}>{qualityNumber(c.metrics.stddev)}</TableCell>
            {(grade === 1 ? [c.metrics.score_sum, c.metrics.max_score, c.metrics.min_score] : averages.map(([field]) => c.metrics[field] as number | null | undefined)).map((v, i) =>
              <TableCell key={i} className="text-right tabular-nums">{qualityNumber(v)}</TableCell>)}
            {bands.map(label => { const band = c.metrics.bands?.find(b => b.label === label); return <Fragment key={label}>
              <TableCell className="text-right tabular-nums">{qualityNumber(band?.count, 0)}</TableCell><TableCell className="text-right tabular-nums">{qualityPercent(band?.ratio)}</TableCell>
            </Fragment> })}
          </TableRow>)}</TableBody>
        </Table>}
      </CardContent>
    </Card>
  })}</>
}

export function TeachingQualitySupplement({ data, basis }: { data: PlatformCompare; basis: 'raw' | 'grade' }) {
  const overall = data.overall[basis]
  const rows = [...data.classes].sort((a, b) => a.class_kind.localeCompare(b.class_kind) || byClassName(a, b))
  return <>
    <p className="text-sm text-slate-600">{basis === 'raw' ? '原始分' : '等级分'}总体均分：<strong className="tabular-nums text-slate-900">{qualityNumber(overall?.value)}</strong>
      <span className="ml-3 text-xs text-slate-500">{!overall ? '覆盖范围尚不能合并，均差显示“—”' : overall.method === 'matching_aggregate' ? '范围匹配的官方汇总' : '同范围官方班级按实考人数加权'}</span></p>
    <p className="text-xs text-slate-500">名次按同来源、同分值类型计算；均差只使用覆盖一致的总体。样本估算与官方指标分别排名。</p>
    <Table className="whitespace-nowrap" aria-label="质量分析补充对比"><TableHeader><TableRow>
      {['班级', '类别', '数据依据', '均分', '名次', '均差'].map(h => <TableHead key={h}>{h}</TableHead>)}
    </TableRow></TableHeader><TableBody>{rows.map(c => <TableRow key={c.id}>
      <TableCell className="font-medium">{c.class_label}</TableCell><TableCell>{c.class_kind === 'teaching' ? '教学班' : '行政班'}</TableCell>
      <TableCell>{sourceLabels[c.source] ?? '尚无统计'}</TableCell>
      <TableCell className="tabular-nums">{qualityNumber(basis === 'raw' ? c.raw_avg : c.grade_avg)}</TableCell>
      <TableCell className="tabular-nums">{qualityNumber(basis === 'raw' ? c.raw_rank : c.grade_rank, 0)}</TableCell>
      <TableCell className="tabular-nums">{qualityNumber(basis === 'raw' ? c.raw_diff : c.grade_diff)}</TableCell>
    </TableRow>)}</TableBody></Table>
  </>
}
