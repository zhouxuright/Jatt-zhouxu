import service from './index'

export const contractApi = {
  /** 同步审查：一次性返回完整结果。上万字合同耗时可能超过反向代理超时，优先用异步版。 */
  reviewContract(formData: FormData) {
    return service.post('/contract/review', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: 120000,
    })
  },
  /**
   * 异步审查：立即返回 202 + task_id，后台执行，配合 getTaskStatus 轮询。
   *
   * 为什么用异步：完整审查会对合同多个条款分别调用大模型，长文本轻松超过
   * 分钟级；同步请求会一直占着连接，被反向代理的超时（120s）掐断成 504，
   * 用户只看到"请求失败"。异步把长耗时从 HTTP 连接里解耦出去。
   */
  reviewContractAsync(formData: FormData) {
    return service.post('/contract/review-async', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: 120000,
    })
  },
  /** 查询异步任务状态（任务状态存于 Redis，多副本间共享）。 */
  getTaskStatus(taskId: string) {
    return service.get(`/tasks/${taskId}`)
  },
  getReviewHistory(page = 1, pageSize = 20) {
    return service.get('/contract/reviews', { params: { page, page_size: pageSize } })
  },
  getReviewDetail(reviewId: string) {
    return service.get(`/contract/reviews/${reviewId}`)
  },
}

export const lifecycleApi = {
  // 合同管理
  listContracts(params: { page?: number; page_size?: number; status?: string } = {}) {
    return service.get('/contract/lifecycle/contracts', { params })
  },
  getContractDetail(contractId: string) {
    return service.get(`/contract/lifecycle/contracts/${contractId}`)
  },
  importContract(data: { title: string; content: string; contract_type?: string }) {
    return service.post('/contract/lifecycle/contracts', data)
  },
  deleteContract(contractId: string) {
    return service.delete(`/contract/lifecycle/contracts/${contractId}`)
  },

  // 生命周期操作
  reviewContract(contractId: string) {
    return service.post(`/contract/lifecycle/contracts/${contractId}/review`, {}, {
      timeout: 180000,
    })
  },
  archiveContract(contractId: string, final_content?: string) {
    return service.post(`/contract/lifecycle/contracts/${contractId}/archive`,
      final_content ? { final_content } : {})
  },

  // 工具（带可选持久化）
  extractDates(data: { contract_text: string; contract_id?: string }) {
    return service.post('/contract/lifecycle/extract-dates', data)
  },
}
