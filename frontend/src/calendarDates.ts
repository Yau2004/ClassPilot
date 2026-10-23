export const calendarWeekdays = ['日', '一', '二', '三', '四', '五', '六'] as const

export function dateKey(year: number, month: number, day: number) {
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

export function monthDates(value: string) {
  const [year, month] = value.split('-').map(Number)
  const firstWeekday = new Date(Date.UTC(year, month - 1, 1)).getUTCDay()
  const gridStart = new Date(Date.UTC(year, month - 1, 1 - firstWeekday))
  return Array.from({ length: 42 }, (_, index) => {
    const cell = new Date(Date.UTC(
      gridStart.getUTCFullYear(),
      gridStart.getUTCMonth(),
      gridStart.getUTCDate() + index,
    ))
    return dateKey(cell.getUTCFullYear(), cell.getUTCMonth() + 1, cell.getUTCDate())
  })
}

export function weekdayIndex(value: string) {
  const [year, month, day] = value.split('-').map(Number)
  return new Date(Date.UTC(year, month - 1, day)).getUTCDay()
}
