import assert from 'node:assert/strict'
import test from 'node:test'
import { genderLabels, shouldDisplaySupportTags, studentDemographicsLabel, studentIdentityContext, supportNeedLabels } from '../src/studentProfileDisplay.ts'

test('support need states remain distinct and only the needs-support state exposes tags', () => {
  assert.equal(supportNeedLabels.not_provided, '未提供相關資料')
  assert.equal(supportNeedLabels.none_reported, '明確表示沒有相關需要')
  assert.equal(supportNeedLabels.needs_support, '有相關教學支援需要')
  assert.equal(shouldDisplaySupportTags('not_provided'), false)
  assert.equal(shouldDisplaySupportTags('none_reported'), false)
  assert.equal(shouldDisplaySupportTags('needs_support'), true)
})

test('workspace demographic labels omit missing values and punctuation', () => {
  assert.deepEqual(genderLabels, {
    female: '女',
    male: '男',
    other: '其他',
    prefer_not_to_say: '不願透露',
  })
  assert.equal(studentDemographicsLabel(null, null), '')
  assert.equal(studentDemographicsLabel(9, null), '9歲')
  assert.equal(studentDemographicsLabel(null, 'female'), '女')
  assert.equal(studentDemographicsLabel(9, 'female'), '9歲，女')
})

test('shared student identity context only mentions reported support needs', () => {
  assert.equal(studentIdentityContext(8, 'male', 'none_reported'), '8歲，男')
  assert.equal(studentIdentityContext(7, 'female', 'needs_support'), '7歲，女，有相關教學支援需要')
  assert.equal(studentIdentityContext(null, null, 'needs_support'), '有相關教學支援需要')
  assert.equal(studentIdentityContext(null, null, 'not_provided'), '')
})
