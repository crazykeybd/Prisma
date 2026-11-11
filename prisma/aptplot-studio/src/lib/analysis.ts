import type {
  Point,
  RegressionSummary,
  StatsSummary,
} from '../types'

function finitePoints(points: Point[]) {
  return points.filter(
    (point) => Number.isFinite(point.x) && Number.isFinite(point.y),
  )
}

function analysisPoints(points: Point[]) {
  const sorted = finitePoints(points).sort((a, b) => a.x - b.x)
  const grouped = new Map<number, { x: number; y: number; count: number }>()

  for (const point of sorted) {
    const existing = grouped.get(point.x)
    if (existing) {
      existing.y += point.y
      existing.count += 1
    } else {
      grouped.set(point.x, { x: point.x, y: point.y, count: 1 })
    }
  }

  return Array.from(grouped.values(), (point) => ({
    x: point.x,
    y: point.y / point.count,
  }))
}

export function summarize(points: Point[]): StatsSummary {
  const values = finitePoints(points).map((point) => point.y)
  if (values.length === 0) {
    return {
      count: 0,
      min: 0,
      max: 0,
      mean: 0,
      median: 0,
      stdDev: 0,
      range: 0,
    }
  }

  const sorted = [...values].sort((a, b) => a - b)
  const min = sorted[0]
  const max = sorted[sorted.length - 1]
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length
  const middle = Math.floor(sorted.length / 2)
  const median =
    sorted.length % 2 === 0
      ? (sorted[middle - 1] + sorted[middle]) / 2
      : sorted[middle]
  const variance =
    values.reduce((sum, value) => sum + (value - mean) ** 2, 0) /
    values.length

  return {
    count: values.length,
    min,
    max,
    mean,
    median,
    stdDev: Math.sqrt(variance),
    range: max - min,
  }
}

export function smoothPoints(points: Point[], windowSize: number): Point[] {
  const window = Math.max(1, Math.floor(windowSize))
  const values = analysisPoints(points)
  if (window <= 1) return values
  const left = Math.floor((window - 1) / 2)
  const right = window - left - 1

  return values.map((point, index) => {
    const start = Math.max(0, index - left)
    const end = Math.min(values.length, index + right + 1)
    const windowPoints = values.slice(start, end)
    const average =
      windowPoints.reduce((sum, candidate) => sum + candidate.y, 0) /
      windowPoints.length
    return { x: point.x, y: average }
  })
}

export function derivativePoints(points: Point[]): Point[] {
  const values = analysisPoints(points)
  if (values.length < 2) return []

  return values.map((point, index) => {
    const previous = values[Math.max(0, index - 1)]
    const next = values[Math.min(values.length - 1, index + 1)]
    const dx = next.x - previous.x
    return {
      x: point.x,
      y: dx === 0 ? 0 : (next.y - previous.y) / dx,
    }
  })
}

export function integralPoints(points: Point[]): Point[] {
  const values = analysisPoints(points)
  if (values.length === 0) return []
  let total = 0
  return values.map((point, index) => {
    if (index > 0) {
      const previous = values[index - 1]
      total += ((previous.y + point.y) / 2) * (point.x - previous.x)
    }
    return { x: point.x, y: total }
  })
}

export function normalizePoints(points: Point[]): Point[] {
  const values = analysisPoints(points)
  const summary = summarize(values)
  const range = summary.max - summary.min
  if (range === 0) return values.map((point) => ({ ...point, y: 0 }))
  return values.map((point) => ({
    x: point.x,
    y: (point.y - summary.min) / range,
  }))
}

export function linearRegression(points: Point[]): {
  points: Point[]
  summary: RegressionSummary
} {
  const values = finitePoints(points)
  if (values.length < 2) {
    throw new Error('线性回归至少需要两个有效数据点。')
  }

  const count = values.length
  const meanX = values.reduce((sum, point) => sum + point.x, 0) / count
  const meanY = values.reduce((sum, point) => sum + point.y, 0) / count
  let numerator = 0
  let denominator = 0
  for (const point of values) {
    numerator += (point.x - meanX) * (point.y - meanY)
    denominator += (point.x - meanX) ** 2
  }

  const slope = denominator === 0 ? 0 : numerator / denominator
  const intercept = meanY - slope * meanX
  const totalSum = values.reduce(
    (sum, point) => sum + (point.y - meanY) ** 2,
    0,
  )
  const residualSum = values.reduce(
    (sum, point) =>
      sum + (point.y - (slope * point.x + intercept)) ** 2,
    0,
  )
  const rSquared = totalSum === 0 ? 1 : 1 - residualSum / totalSum
  const minX = Math.min(...values.map((point) => point.x))
  const maxX = Math.max(...values.map((point) => point.x))

  return {
    points: [
      { x: minX, y: slope * minX + intercept },
      { x: maxX, y: slope * maxX + intercept },
    ],
    summary: {
      slope,
      intercept,
      rSquared,
      equation: `y = ${slope.toPrecision(5)}x ${
        intercept >= 0 ? '+' : '-'
      } ${Math.abs(intercept).toPrecision(5)}`,
    },
  }
}

function nextPowerOfTwo(value: number) {
  return 2 ** Math.ceil(Math.log2(Math.max(2, value)))
}

function fft(real: Float64Array, imaginary: Float64Array) {
  const length = real.length
  for (let index = 1, reverse = 0; index < length; index += 1) {
    let bit = length >> 1
    for (; reverse & bit; bit >>= 1) reverse ^= bit
    reverse ^= bit
    if (index < reverse) {
      ;[real[index], real[reverse]] = [real[reverse], real[index]]
      ;[imaginary[index], imaginary[reverse]] = [
        imaginary[reverse],
        imaginary[index],
      ]
    }
  }

  for (let size = 2; size <= length; size <<= 1) {
    const angle = (-2 * Math.PI) / size
    const half = size >> 1
    for (let start = 0; start < length; start += size) {
      for (let offset = 0; offset < half; offset += 1) {
        const cosine = Math.cos(angle * offset)
        const sine = Math.sin(angle * offset)
        const evenIndex = start + offset
        const oddIndex = evenIndex + half
        const oddReal =
          real[oddIndex] * cosine - imaginary[oddIndex] * sine
        const oddImaginary =
          real[oddIndex] * sine + imaginary[oddIndex] * cosine
        real[oddIndex] = real[evenIndex] - oddReal
        imaginary[oddIndex] = imaginary[evenIndex] - oddImaginary
        real[evenIndex] += oddReal
        imaginary[evenIndex] += oddImaginary
      }
    }
  }
}

export function fftMagnitude(points: Point[]): Point[] {
  const values = analysisPoints(points)
  if (values.length < 4) {
    throw new Error('FFT 至少需要四个有效数据点。')
  }

  const deltas = values
    .slice(1)
    .map((point, index) => point.x - values[index].x)
    .filter((delta) => delta > 0)
    .sort((a, b) => a - b)
  const sampleInterval = deltas[Math.floor(deltas.length / 2)] ?? 1
  const span = values[values.length - 1].x - values[0].x
  if (span <= 0) {
    throw new Error('FFT 需要至少两个不同的横坐标值。')
  }

  const desiredSamples = Math.round(span / sampleInterval) + 1
  const sampleCount = Math.min(8192, Math.max(4, desiredSamples))
  const effectiveInterval = span / (sampleCount - 1)
  const samples = new Float64Array(sampleCount)
  let sourceIndex = 0
  for (let index = 0; index < sampleCount; index += 1) {
    const x = values[0].x + index * effectiveInterval
    while (
      sourceIndex + 1 < values.length - 1 &&
      values[sourceIndex + 1].x < x
    ) {
      sourceIndex += 1
    }
    const left = values[sourceIndex]
    const right = values[Math.min(sourceIndex + 1, values.length - 1)]
    const width = right.x - left.x
    const ratio = width === 0 ? 0 : (x - left.x) / width
    samples[index] = left.y + (right.y - left.y) * ratio
  }

  const size = nextPowerOfTwo(sampleCount)
  const real = new Float64Array(size)
  const imaginary = new Float64Array(size)
  let windowSum = 0

  for (let index = 0; index < sampleCount; index += 1) {
    const hann =
      0.5 - 0.5 * Math.cos((2 * Math.PI * index) / (sampleCount - 1))
    real[index] = samples[index] * hann
    windowSum += hann
  }

  fft(real, imaginary)
  const output: Point[] = []
  for (let index = 1; index < size / 2; index += 1) {
    output.push({
      x: index / (size * effectiveInterval),
      y: (2 * Math.hypot(real[index], imaginary[index])) / windowSum,
    })
  }
  return output
}
