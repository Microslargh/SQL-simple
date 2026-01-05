import { requestF } from '@/utils/request'

export const promptApi = {
  getList: (pageNum: any, pageSize: any, keywords: any) => {
    return requestF.get(
      `/fin/cud/assocPrompt/page?pageNo=${pageNum}&pageSize=${pageSize}&promptContent=${keywords}`
    )
  },
  addEmbedded: (data: any) => requestF.post(`/fin/cud/assocPrompt/add`, data),
  updateEmbedded: (data: any) => requestF.post(`/fin/cud/assocPrompt/update`, data),
  deleteEmbedded: (params: any) =>
    requestF.post(`/fin/cud/assocPrompt/delete/${params.id}`, params),
  getMatch: (keyword: any) => requestF.get(`/fin/cud/assocPrompt/match?keyword=${keyword}&size=5`),
}
export const hot_Question_Api = {
  getList: (pageNum: any, pageSize: any, keywords: any) => {
    return requestF.get(
      `/fin/cud/hotQuestion/page?pageNo=${pageNum}&pageSize=${pageSize}&title=${keywords}`
    )
  },
  addEmbedded: (data: any) => requestF.post(`/fin/cud/hotQuestion/add`, data),
  updateEmbedded: (data: any) => requestF.post(`/fin/cud/hotQuestion/update`, data),
  deleteEmbedded: (params: any) =>
    requestF.post(`/fin/cud/hotQuestion/delete/${params.id}`, params),
  getMatch: () => requestF.get(`/fin/cud/hotQuestion/topList?size=10`),
}
