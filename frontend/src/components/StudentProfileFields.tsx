import type { StudentProfile } from '../api'
import { shouldDisplaySupportTags, studentDemographicsLabel } from '../studentProfileDisplay'

export function StudentIdentityLine({ student, nameId }: { student: StudentProfile; nameId?: string }) {
  const demographics = studentDemographicsLabel(student.age, student.gender)
  const needsSupport = shouldDisplaySupportTags(student.support_need_status)
  return <div className="student-identity-line">
    <strong id={nameId}>{student.name}</strong>
    {demographics && <span>{demographics}</span>}
    {needsSupport && <span className="student-support-indicator">{demographics ? '，' : ''}有相關教學支援需要</span>}
  </div>
}

export function StudentTeachingInformation({ student, id }: { student: StudentProfile; id?: string }) {
  return <div className="student-teaching-information" id={id}>
    {shouldDisplaySupportTags(student.support_need_status) && <div><strong>支援標籤</strong><span>{student.support_need_tags.length ? student.support_need_tags.join('、') : '未填寫'}</span></div>}
    <div><strong>教學注意事項</strong><span>{student.support_notes?.trim() || '未填寫'}</span></div>
    <div><strong>要教學的內容</strong><span>{student.general_notes?.trim() || '未填寫'}</span></div>
    {student.is_archived && <span className="student-archive-badge">已封存</span>}
  </div>
}
