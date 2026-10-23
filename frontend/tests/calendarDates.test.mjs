import assert from 'node:assert/strict'
import test from 'node:test'
import { calendarWeekdays, monthDates, weekdayIndex } from '../src/calendarDates.ts'

test('calendar starts Sunday and aligns every date with its weekday', () => {
  assert.deepEqual(calendarWeekdays, ['日', '一', '二', '三', '四', '五', '六'])
  for (const month of ['2026-11', '2026-05', '2027-01', '2026-12']) {
    const cells = monthDates(month)
    assert.equal(cells.length, 42)
    assert.equal(weekdayIndex(cells[0]), 0)
    for (let index = 0; index < cells.length; index += 1) {
      assert.equal(weekdayIndex(cells[index]), index % 7, `${month} cell ${index}`)
      if (index > 0) {
        const previous = new Date(`${cells[index - 1]}T00:00:00Z`)
        previous.setUTCDate(previous.getUTCDate() + 1)
        assert.equal(cells[index], previous.toISOString().slice(0, 10))
      }
    }
  }
})

test('month grid includes correctly ordered adjacent-month dates across year boundaries', () => {
  assert.equal(monthDates('2026-11')[0], '2026-11-01')
  assert.equal(monthDates('2026-05')[0], '2026-04-26')
  assert.equal(monthDates('2027-01')[0], '2026-12-27')
  assert.equal(monthDates('2026-12').at(-1), '2027-01-09')
})
