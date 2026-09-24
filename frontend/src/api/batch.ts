import service from './index'

export interface FileResult {
  filename: string
  status: string
  text?: string
  summary?: string
  key_points?: string
  error?: string
  processing_time_seconds?: number
  document_id?: string
}

export interface BatchUploadResponse {
  batch_id: string
  total_files: number
  status: string
  message: string
}

export interface BatchStatusResponse {
  batch_id: string
  status: string
  total_files: number
  completed_files: number
  failed_files: number
  progress_percent: number
  mode: string
  started_at: string
  completed_at: string | null
  results: FileResult[]
}

export interface BatchResultsResponse extends BatchStatusResponse {
  results: FileResult[]
}

export interface DocumentItem {
  id: string
  filename: string
  original_filename: string
  file_type: string
  file_size: number
  status: string
  summary: string | null
  error_message: string | null
  upload_time: string
  batch_id: string | null
}

export interface DocumentHistoryResponse {
  documents: DocumentItem[]
  total: number
  page: number
  page_size: number
}

export const batchApi = {
  uploadBatch(files: File[], mode: string = 'analyze') {
    const formData = new FormData()
    files.forEach((file) => {
      formData.append('files', file)
    })
    formData.append('mode', mode)
    return service.post<BatchUploadResponse>('/document/batch-upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  getBatchStatus(batchId: string) {
    return service.get<BatchStatusResponse>(`/document/batch-status/${batchId}`)
  },

  getBatchResults(batchId: string) {
    return service.get<BatchResultsResponse>(`/document/batch-results/${batchId}`)
  },

  downloadBatchResults(batchId: string) {
    return service.get(`/document/batch-download/${batchId}`, {
      responseType: 'blob',
    })
  },

  getDocumentHistory(page: number = 1, pageSize: number = 20) {
    return service.get<DocumentHistoryResponse>('/document/history', {
      params: { page, page_size: pageSize },
    })
  },

  getDocument(documentId: string) {
    return service.get(`/document/${documentId}`)
  },
}
