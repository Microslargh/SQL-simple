import { request } from '@/utils/request'

export interface ChatExecutionTraceItem {
  id?: number
  record_id: number
  chat_id: number
  create_by?: number
  trace_group: string
  node_key: string
  node_name: string
  status: 'running' | 'success' | 'error' | string
  input_payload?: Record<string, any> | null
  output_payload?: Record<string, any> | null
  extra_data?: Record<string, any> | null
  error_message?: string | null
  start_time?: string
  finish_time?: string | null
  duration_ms?: number | null
}

export const chatTraceApi = {
  getByRecordId: (recordId: number) =>
    request.get<ChatExecutionTraceItem[]>(`/chat/record/${recordId}/trace`),
}
