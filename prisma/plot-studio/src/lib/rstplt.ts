import type { CellValue, DataColumn, DataRow, DataSet } from '../types'

const FINGERPRINT = 'restart-plot file'
const READ_CHUNK_SIZE = 8 * 1024 * 1024
const MAX_METADATA_RECORD = 4 * 1024 * 1024
const PROGRESS_BYTES = 64 * 1024 * 1024
const PROGRESS_INTERVAL_MS = 100

const PLOTINF = 'plotinf '
const PLOTALF = 'plotalf '
const PLOTNUM = 'plotnum '
const PLOTREC = 'plotrec '
const RESTART = 'restart '

export class RstpltFormatError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'RstpltFormatError'
  }
}

export interface RstpltVariableOccurrence {
  segmentIndex: number
  variableIndex: number
  recordCount: number
}

export interface RstpltVariable {
  id: string
  alpha: string
  number: number
  code: string
  isTime: boolean
  recordCount: number
  occurrences: RstpltVariableOccurrence[]
}

export interface RstpltRecord {
  payloadOffset: number
  payloadLength: number
}

export interface RstpltSegment {
  index: number
  variables: Omit<RstpltVariable, 'occurrences' | 'recordCount'>[]
  records: RstpltRecord[]
  payloadLength: number
}

export interface RstpltCatalog {
  fileName: string
  fileSize: number
  programId: string
  fileDate: string
  problemType: number
  problemTypeName: string
  restartCount: number
  recordCount: number
  segments: RstpltSegment[]
  variables: RstpltVariable[]
}

export type RstpltProgress = (done: number, total: number) => void

interface IdRecord {
  programId: string
  fileDate: string
  problemType: number
  nextOffset: number
}

interface PendingSegment {
  index: number
  alphas: string[]
  numbers: number[]
  records: RstpltRecord[]
  payloadLength: number
}

class BufferedFileReader {
  private readonly file: File
  private buffer = new Uint8Array()
  private bufferStart = 0

  constructor(file: File) {
    this.file = file
  }

  async bytes(offset: number, length: number): Promise<Uint8Array> {
    if (
      offset < 0 ||
      length < 0 ||
      offset + length > this.file.size ||
      !Number.isSafeInteger(offset + length)
    ) {
      throw new RstpltFormatError('RSTPLT 文件记录越界，文件可能不完整。')
    }

    const bufferEnd = this.bufferStart + this.buffer.length
    if (offset >= this.bufferStart && offset + length <= bufferEnd) {
      return this.buffer.subarray(offset - this.bufferStart, offset - this.bufferStart + length)
    }

    const readLength = Math.max(READ_CHUNK_SIZE, length)
    const end = Math.min(this.file.size, offset + readLength)
    const data = await this.file.slice(offset, end).arrayBuffer()
    this.buffer = new Uint8Array(data)
    this.bufferStart = offset

    if (length > this.buffer.length) {
      throw new RstpltFormatError('RSTPLT 文件记录读取不完整。')
    }
    return this.buffer.subarray(0, length)
  }

  async uint32(offset: number): Promise<number> {
    const data = await this.bytes(offset, 4)
    return new DataView(data.buffer, data.byteOffset, data.byteLength).getUint32(
      0,
      true,
    )
  }
}

function decodeLatin1(data: Uint8Array): string {
  return new TextDecoder('latin1').decode(data)
}

function startsWith(data: Uint8Array, prefix: string): boolean {
  if (data.length < prefix.length) return false
  for (let index = 0; index < prefix.length; index += 1) {
    if (data[index] !== prefix.charCodeAt(index)) return false
  }
  return true
}

function readInt32(data: Uint8Array, offset: number): number {
  if (offset < 0 || offset + 4 > data.length) {
    throw new RstpltFormatError('RSTPLT 元数据不完整。')
  }
  return new DataView(data.buffer, data.byteOffset, data.byteLength).getInt32(
    offset,
    true,
  )
}

function readFloat32(data: Uint8Array, offset: number): number {
  return new DataView(data.buffer, data.byteOffset, data.byteLength).getFloat32(
    offset,
    true,
  )
}

async function readRecordPayload(
  reader: BufferedFileReader,
  payloadOffset: number,
  payloadLength: number,
): Promise<Uint8Array> {
  if (payloadLength > MAX_METADATA_RECORD) {
    throw new RstpltFormatError('RSTPLT 元数据记录异常过大。')
  }
  return reader.bytes(payloadOffset, payloadLength)
}

async function validateRecord(
  reader: BufferedFileReader,
  fileSize: number,
  recordStart: number,
): Promise<{ payloadOffset: number; payloadLength: number; nextOffset: number }> {
  const payloadLength = await reader.uint32(recordStart)
  const payloadOffset = recordStart + 4
  const payloadEnd = payloadOffset + payloadLength
  const nextOffset = payloadEnd + 4

  if (payloadLength > fileSize || nextOffset > fileSize) {
    throw new RstpltFormatError(
      `RSTPLT 记录长度非法（偏移 ${recordStart}），文件可能损坏。`,
    )
  }

  const trailer = await reader.uint32(payloadEnd)
  if (trailer !== payloadLength) {
    throw new RstpltFormatError(
      `RSTPLT 记录尾缀不匹配（偏移 ${recordStart}），文件可能损坏。`,
    )
  }

  return { payloadOffset, payloadLength, nextOffset }
}

async function readIdRecord(
  reader: BufferedFileReader,
  file: File,
): Promise<IdRecord> {
  let recordStart = 0
  let record = await validateRecord(reader, file.size, recordStart)

  if (record.payloadLength === 8) {
    recordStart = record.nextOffset
    if (recordStart >= file.size) {
      throw new RstpltFormatError('RSTPLT 文件头缺少标识记录。')
    }
    record = await validateRecord(reader, file.size, recordStart)
  }

  if (record.payloadLength !== 80) {
    throw new RstpltFormatError(
      `文件头不符合 RSTPLT 格式（标识记录为 ${record.payloadLength} 字节，预期 80）。`,
    )
  }

  const payload = await readRecordPayload(
    reader,
    record.payloadOffset,
    record.payloadLength,
  )
  const fingerprint = decodeLatin1(payload.subarray(24, 48))
  if (!fingerprint.includes(FINGERPRINT)) {
    throw new RstpltFormatError(
      "标识记录中未找到 'restart-plot file'，不是 RELAP5 RSTPLT 文件。",
    )
  }

  return {
    programId: decodeLatin1(payload.subarray(0, 24)).trim(),
    fileDate: decodeLatin1(payload.subarray(48, 72)).trim(),
    problemType: readInt32(payload, 76),
    nextOffset: record.nextOffset,
  }
}

function variableCode(alpha: string, number: number): string {
  return number === 0 ? alpha : `${alpha} ${number}`
}

function variableId(alpha: string, number: number): string {
  return `${number}:${alpha}`
}

function problemTypeName(problemType: number): string {
  if (problemType === 1) return '稳态'
  if (problemType === 2) return '瞬态'
  return `类型 ${problemType}`
}

function makeProgressReporter(onProgress?: RstpltProgress) {
  let lastBytes = 0
  let lastTime = 0

  return (done: number, total: number, force = false) => {
    if (!onProgress) return
    const now = Date.now()
    if (
      !force &&
      done - lastBytes < PROGRESS_BYTES &&
      now - lastTime < PROGRESS_INTERVAL_MS
    ) {
      return
    }
    lastBytes = done
    lastTime = now
    onProgress(done, total)
  }
}

export function isRstpltFilename(fileName: string): boolean {
  const lower = fileName.toLowerCase()
  return (
    lower.endsWith('.rstplt') ||
    lower === 'rstplt' ||
    lower.endsWith('.rstrt') ||
    lower === 'rstrt'
  )
}

export async function sniffRstpltFile(file: File): Promise<boolean> {
  if (file.size < 92) return false
  try {
    await readIdRecord(new BufferedFileReader(file), file)
    return true
  } catch {
    return false
  }
}

export async function scanRstpltFile(
  file: File,
  onProgress?: RstpltProgress,
): Promise<RstpltCatalog> {
  if (file.size < 92) {
    throw new RstpltFormatError('文件太小，不是有效的 RELAP5 RSTPLT 文件。')
  }

  const reader = new BufferedFileReader(file)
  const idRecord = await readIdRecord(reader, file)
  const reportProgress = makeProgressReporter(onProgress)
  const segments: RstpltSegment[] = []
  const pendingSegments: PendingSegment[] = []
  let current: PendingSegment | null = null
  let plotRecordLength = 0
  let restartCount = 0
  let recordCount = 0
  let position = idRecord.nextOffset

  while (position < file.size) {
    const recordStart = position
    const record = await validateRecord(reader, file.size, recordStart)
    position = record.nextOffset

    const header = await reader.bytes(
      record.payloadOffset,
      Math.min(8, record.payloadLength),
    )

    if (startsWith(header, PLOTINF)) {
      if (record.payloadLength < 24) {
        throw new RstpltFormatError('plotinf 记录长度不足。')
      }
      const payload = await readRecordPayload(
        reader,
        record.payloadOffset,
        record.payloadLength,
      )
      const variableCount = readInt32(payload, 12)
      const wordCount = readInt32(payload, 20)
      plotRecordLength = wordCount * 8
      if (
        variableCount <= 0 ||
        plotRecordLength < 12 ||
        plotRecordLength > file.size
      ) {
        throw new RstpltFormatError('plotinf 中的变量数或记录长度无效。')
      }
    } else if (startsWith(header, PLOTALF)) {
      const payload = await readRecordPayload(
        reader,
        record.payloadOffset,
        record.payloadLength,
      )
      if (plotRecordLength === 0) {
        throw new RstpltFormatError('plotalf 出现在 plotinf 之前。')
      }
      const dataLength = payload.length - 8
      if (dataLength < 8 || dataLength % 8 !== 0) {
        throw new RstpltFormatError('plotalf 变量目录长度无效。')
      }
      const alphas: string[] = []
      for (let offset = 8; offset < payload.length; offset += 8) {
        const alpha = decodeLatin1(payload.subarray(offset, offset + 8)).trim()
        if (!alpha) {
          throw new RstpltFormatError('plotalf 中包含空变量名。')
        }
        alphas.push(alpha)
      }
      current = {
        index: pendingSegments.length,
        alphas,
        numbers: [],
        records: [],
        payloadLength: plotRecordLength,
      }
      pendingSegments.push(current)
    } else if (startsWith(header, PLOTNUM)) {
      const payload = await readRecordPayload(
        reader,
        record.payloadOffset,
        record.payloadLength,
      )
      if (!current) {
        throw new RstpltFormatError('plotnum 出现在变量目录之前。')
      }
      const dataLength = payload.length - 8
      if (dataLength < 8 || dataLength % 8 !== 0) {
        throw new RstpltFormatError('plotnum 变量编号目录长度无效。')
      }
      const numbers: number[] = []
      for (let offset = 8; offset < payload.length; offset += 8) {
        numbers.push(readInt32(payload, offset + 4))
      }
      current.numbers = numbers
    } else if (startsWith(header, PLOTREC)) {
      if (!current) {
        throw new RstpltFormatError('plotrec 出现在变量目录之前。')
      }
      if (record.payloadLength !== current.payloadLength) {
        throw new RstpltFormatError(
          `plotrec 数据长度不一致（偏移 ${recordStart}）。`,
        )
      }
      current.records.push({
        payloadOffset: record.payloadOffset,
        payloadLength: record.payloadLength,
      })
      recordCount += 1
    } else if (startsWith(header, RESTART)) {
      restartCount += 1
    }

    reportProgress(position, file.size)
  }

  if (pendingSegments.length === 0) {
    throw new RstpltFormatError('文件中没有找到 plotalf/plotnum 变量目录。')
  }

  const variablesById = new Map<string, RstpltVariable>()
  for (const pending of pendingSegments) {
    if (
      pending.alphas.length === 0 ||
      pending.alphas.length !== pending.numbers.length
    ) {
      throw new RstpltFormatError(
        `变量目录不完整（plotalf ${pending.alphas.length} 项 / plotnum ${pending.numbers.length} 项）。`,
      )
    }
    if (pending.records.length === 0) {
      throw new RstpltFormatError('变量目录后没有可读取的 plotrec 数据。')
    }

    const variables = pending.alphas.map((alpha, variableIndex) => {
      const number = pending.numbers[variableIndex]
      const id = variableId(alpha, number)
      const isTime = alpha.toLowerCase() === 'time' && number === 0
      const variable: RstpltVariable = {
        id,
        alpha,
        number,
        code: variableCode(alpha, number),
        isTime,
        recordCount: 0,
        occurrences: [],
      }
      const merged = variablesById.get(id)
      if (merged) {
        merged.recordCount += pending.records.length
        merged.occurrences.push({
          segmentIndex: pending.index,
          variableIndex,
          recordCount: pending.records.length,
        })
        return merged
      }

      variable.recordCount = pending.records.length
      variable.occurrences.push({
        segmentIndex: pending.index,
        variableIndex,
        recordCount: pending.records.length,
      })
      variablesById.set(id, variable)
      return variable
    })

    segments.push({
      index: pending.index,
      variables: variables.map((variable) => ({
        id: variable.id,
        alpha: variable.alpha,
        number: variable.number,
        code: variable.code,
        isTime: variable.isTime,
      })),
      records: pending.records,
      payloadLength: pending.payloadLength,
    })
  }

  if (recordCount === 0) {
    throw new RstpltFormatError('文件中没有找到可绘制的 plotrec 数据。')
  }

  const variables = Array.from(variablesById.values()).sort((left, right) => {
    if (left.isTime !== right.isTime) return left.isTime ? -1 : 1
    return (
      left.alpha.localeCompare(right.alpha, 'en', { numeric: true }) ||
      left.number - right.number
    )
  })

  reportProgress(file.size, file.size, true)

  return {
    fileName: file.name,
    fileSize: file.size,
    programId: idRecord.programId,
    fileDate: idRecord.fileDate,
    problemType: idRecord.problemType,
    problemTypeName: problemTypeName(idRecord.problemType),
    restartCount,
    recordCount,
    segments,
    variables,
  }
}

function safeFloat(value: number): CellValue {
  return Number.isFinite(value) ? value : null
}

function makeDatasetId() {
  return `rstplt-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function yieldToBrowser() {
  return new Promise<void>((resolve) => window.setTimeout(resolve, 0))
}

interface ReadBatch {
  start: number
  end: number
  records: RstpltRecord[]
}

function makeReadBatches(records: RstpltRecord[]): ReadBatch[] {
  const batches: ReadBatch[] = []
  let current: ReadBatch | null = null

  for (const record of records) {
    const recordEnd = record.payloadOffset + record.payloadLength
    if (
      !current ||
      recordEnd - current.start > READ_CHUNK_SIZE
    ) {
      if (current) batches.push(current)
      current = {
        start: record.payloadOffset,
        end: recordEnd,
        records: [record],
      }
    } else {
      current.end = recordEnd
      current.records.push(record)
    }
  }

  if (current) batches.push(current)
  return batches
}

export async function loadRstpltDataset(
  file: File,
  catalog: RstpltCatalog,
  selectedVariableIds: string[],
  onProgress?: RstpltProgress,
): Promise<DataSet> {
  const selectedSet = new Set(selectedVariableIds)
  const selectedVariables = catalog.variables.filter(
    (variable) => !variable.isTime && selectedSet.has(variable.id),
  )
  if (selectedVariables.length === 0) {
    throw new Error('请至少选择一条 RSTPLT 曲线。')
  }

  const keys = new Map(
    selectedVariables.map((variable, index) => [variable.id, `c${index + 2}`]),
  )
  const rows: DataRow[] = []
  const reader = new BufferedFileReader(file)
  const totalRecords = catalog.recordCount
  let processedRecords = 0
  let batchCounter = 0
  const reportProgress = makeProgressReporter(onProgress)

  for (const segment of catalog.segments) {
    const selectedOccurrences = new Map<number, string>()
    for (const variable of selectedVariables) {
      const occurrence = variable.occurrences.find(
        (item) => item.segmentIndex === segment.index,
      )
      if (occurrence) {
        const key = keys.get(variable.id)
        if (key) selectedOccurrences.set(occurrence.variableIndex, key)
      }
    }

    const batches = makeReadBatches(segment.records)
    for (const batch of batches) {
      const data = await reader.bytes(batch.start, batch.end - batch.start)
      for (const record of batch.records) {
        const localOffset = record.payloadOffset - batch.start
        const timeOffset = localOffset + 8
        if (timeOffset + 4 > data.length) {
          throw new RstpltFormatError('plotrec 时间值不完整。')
        }

        const row: DataRow = {
          c1: safeFloat(readFloat32(data, timeOffset)),
        }
        for (const variable of selectedVariables) {
          row[keys.get(variable.id) ?? ''] = null
        }
        for (const [variableIndex, key] of selectedOccurrences) {
          const valueOffset = localOffset + 8 + variableIndex * 4
          if (valueOffset + 4 > data.length) {
            throw new RstpltFormatError(
              `plotrec 变量 ${variableIndex} 的数据不完整。`,
            )
          }
          row[key] = safeFloat(readFloat32(data, valueOffset))
        }
        rows.push(row)
        processedRecords += 1
      }

      reportProgress(processedRecords, totalRecords)
      batchCounter += 1
      if (batchCounter % 4 === 0) await yieldToBrowser()
    }
  }

  const columns: DataColumn[] = [
    {
      key: 'c1',
      label: 'time',
      type: 'number',
      missing: rows.filter((row) => row.c1 === null).length,
    },
    ...selectedVariables.map((variable) => {
      const key = keys.get(variable.id) ?? ''
      return {
        key,
        label: variable.code,
        type: 'number' as const,
        missing: rows.filter((row) => row[key] === null).length,
      }
    }),
  ]

  reportProgress(totalRecords, totalRecords, true)

  return {
    id: makeDatasetId(),
    name: file.name.replace(/\.[^.]+$/, '') || file.name,
    fileName: file.name,
    importedAt: new Date().toISOString(),
    columns,
    rows,
    xColumn: 'c1',
    yColumns: selectedVariables.map(
      (variable) => keys.get(variable.id) ?? '',
    ),
    visible: true,
  }
}
