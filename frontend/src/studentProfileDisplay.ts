import type { StudentGender, SupportNeedStatus } from './api'

export const supportNeedLabels: Record<SupportNeedStatus, string> = {
  not_provided: '未提供相關資料',
  none_reported: '明確表示沒有相關需要',
  needs_support: '有相關教學支援需要',
}

export const genderLabels: Record<StudentGender, string> = {
  female: '女',
  male: '男',
  other: '其他',
  prefer_not_to_say: '不願透露',
}

export function shouldDisplaySupportTags(status: SupportNeedStatus) {
  return status === 'needs_support'
}

export function studentDemographicsLabel(age: number | null, gender: StudentGender | null) {
  const parts = [age === null ? '' : `${age}歲`, gender === null ? '' : genderLabels[gender]].filter(Boolean)
  return parts.join('，')
}

export function studentIdentityContext(
  age: number | null,
  gender: StudentGender | null,
  status: SupportNeedStatus,
) {
  const parts = [studentDemographicsLabel(age, gender)]
  if (shouldDisplaySupportTags(status)) parts.push('有相關教學支援需要')
  return parts.filter(Boolean).join('，')
}
