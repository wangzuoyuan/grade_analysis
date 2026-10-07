import { redirect } from 'next/navigation'

/** 该层级历史上无页面（仅有 report/ 与 diagnosis-report/ 子路由），重定向兜底防直链/错链 404。
 *  必须拼含 id 的绝对路径：相对 './report' 运行时会解析为 /homeroom/students/report
 *  （丢动态段，再次命中本 [id] 路由 → 循环重定向白屏，实测踩过）。 */
export default function HomeroomStudentIndexPage({ params }: { params: { id: string } }) {
  redirect(`/homeroom/students/${encodeURIComponent(params.id)}/report`)
}
