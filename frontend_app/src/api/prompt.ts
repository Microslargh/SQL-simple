import { request,requestF } from '@/utils/request'

export const promptApi = {
  getList: (pageNum: any, pageSize: any, type: any, params: any) =>
    request.get(`/system/custom_prompt/${type}/page/${pageNum}/${pageSize}`, {
      params,
    }),
  updateEmbedded: (data: any) => request.put(`/system/custom_prompt`, data),
  deleteEmbedded: (params: any) => request.delete('/system/custom_prompt', { data: params }),
  getOne: (id: any) => request.get(`/system/custom_prompt/${id}`),
  getMatch: (keyword: any) => requestF.get(`/fin/cud/assocPrompt/match?keyword=${keyword}&size=5`),
  // getAudioToText: (params: any) => requestF.upload(`/apphost/FIN/fin/cud/other/audioToText`,params) 
  getAudioToText: (params: any) => requestF.upload(`/fin/cud/other/audioToText`,params)
}
export const hot_Question_Api = {
  getMatch: () => requestF.get(`/fin/cud/hotQuestion/topList?size=8`),
}
export const uer_info_Api = {
  uerInfo: (code: any) => requestF.get(`/fin/cud/other/quickAuthen?code=${code}`),
  jobNumber: (code: any) => requestF.get(`/fin/cud/other/quickLogin?jobnumber=${code}`),
}