# 教学平台单科导入与班级对比

## 范围

仅 teaching，服务端校验配置学科，显式 academic_year_id、grade(1/2/3)、subject、exam_name、exam_date。学生文件需教学班 ID，质量表可单独导入。所有标签大小写敏感，不根据样表固定班名或数量。

## 两阶段导入

- POST /api/v1/teaching/platform/imports/preview multipart: files、上述范围、teaching_class_id?、operation(initial|supplement)。仅写预览台账，返回 token、items、changes、conflicts、roster_changes、warnings；保留文件/表/行来源。
- POST /api/v1/teaching/platform/imports/confirm JSON: token、revise=false、accept_roster_changes=false、identity_confirmations?、identity_new_aliases?。复用身份候选规则，锁定预览数据版本和成员/关联快照；任意冲突整批拒绝。新班参照变化必须显式确认。
- initial：新建事实；已有事实按文件实际非空字段合并，空白不清空；不同值须 revise。supplement：只能修改 grade_score(高二三)、xueji_rank/grade_percentile(高一)；原始分不同拒绝，无已有事实拒绝，不创建成员。
- 数值 0 有效，空白不同于 0，NaN/inf/非法数字拒绝；百分位与比例规范化为 0..1。重复自然键、同值跳过；同批互补合并；同字段异值冲突。令牌单次消费，事务失败回滚，版本漂移须重新预览。
- replace_roster=true 是显式分班变化：仅替换本批质量表所提供类别，预览列出移出/纳入并确认；默认补充保留。已存在考试的名单修订同时要求 revise。修订旧考试不更新由更新考试建立的最新参照；旧快照保留在确认回执。

## 读取

- GET /api/v1/teaching/platform/exams?academic_year_id=&grade=&subject=：包含仅有质量表的考试。
- GET /api/v1/teaching/platform/exams/{id}/compare：classes、metadata、overall(raw/grade)、warnings。班级快照与当前参照分离；official / estimated 明确标识；同来源同口径排名；缺值 null。
- GET /api/v1/teaching/platform/exams/{id}/students?teaching_class_id=：仅本人合法成员成绩与允许投影；rank 为本班名次，xueji_rank 为来源学籍排名，grade_percentile 为来源百分位，grade_label 仅由已导入等级分精确映射。
- 质量表中的小计/合计作为 aggregate 单独保留及覆盖说明，不作班级。高一四个分数段不要求人数合计等于实考；高二三等级人数与比率做完整性检查，保留官方精度。
- 合计行 A:B 合并时字段左移一列，按合并区域定位；覆盖人数核对成功的汇总才可作为总体依据。同覆盖汇总出现不同均分时总体/均差不可用。教学班分组没有互斥证据和匹配汇总时不直接加权。
- 总体：匹配同范围官方合计优先；否则仅完整、不重叠同类班按实考人数加权，等级均分需等级人数覆盖完整。混合教学/行政班覆盖不明时不可合并。均差使用同 source/basis/coverage 的总体。

## UI

/upload 教学平台入口及旧模板切换；/compare 官方质量对比及旧样本入口。学年/年级/学科/考试显式，成绩与汇总单独加载；学生表无数据不阻断官方对比。快速切换丢弃迟到响应，变更输入使预览失效。
