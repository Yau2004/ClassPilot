import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const appSource = await readFile(new URL('../src/App.tsx', import.meta.url), 'utf8')
const styleSource = await readFile(new URL('../src/styles.css', import.meta.url), 'utf8')

test('main navigation retains current workflows without legacy page routes', () => {
  assert.match(appSource, /CalendarPage/)
  assert.match(appSource, /StudentsPage/)
  assert.doesNotMatch(appSource, /AttendancePage|ClassesPage|legacyPage|legacy-subnav/)
})

test('decorative slogan markup and dedicated styles are absent', () => {
  assert.doesNotMatch(appSource, /heading-stamp|footer-star/)
  assert.doesNotMatch(styleSource, /heading-stamp|footer-star/)
})
