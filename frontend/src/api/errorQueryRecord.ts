import { request } from '@/utils/request'

export interface ErrorQueryRecordItem {
  id?: number
  record_id: number
  chat_id: number
  question: string
  sql: string
  analysis_text?: string
  error_message: string
  feedback_reason: string
  status?: string
  create_by?: number
  create_time?: string
}

export const errorQueryRecordApi = {
  pager: (pageNum: number, pageSize: number, status?: string, feedback_reason?: string) => {
    const params: Record<string, string> = {}
    if (status) params.status = status
    if (feedback_reason) params.feedback_reason = feedback_reason
    return request.get<{ items: ErrorQueryRecordItem[]; total: number; page: number; size: number; total_pages: number }>(
      `/system/error-query-records/page/${pageNum}/${pageSize}`,
      { params: Object.keys(params).length ? params : undefined }
    )
  },
  updateStatus: (id: number, status: 'pending' | 'resolved') =>
    request.patch(`/system/error-query-records/${id}`, { status }),
  delete: (id: number) => request.delete(`/system/error-query-records/${id}`),
}
