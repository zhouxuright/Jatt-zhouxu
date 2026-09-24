import service from './index'

// ============================================================================
// 数据资产治理 / 合规审计（P0-2 / P0-4 / P2-7）
// ============================================================================

export const adminApi = {
  /** 数据资产登记账本：语料来源、可援引性、条数、完成度 */
  getCorpusRegistry() {
    return service.get('/admin/corpus-registry')
  },

  /** 审计日志分页查询（默认本租户） */
  listAuditLogs(params: {
    page?: number
    page_size?: number
    user_id?: string
    action?: string
    status_code?: number
    start_date?: string
    end_date?: string
    all_tenants?: boolean
  } = {}) {
    return service.get('/admin/audit-logs', { params })
  },

  /** 合规审计日志导出（csv | json），返回 Blob */
  exportAuditLogs(params: {
    format?: 'csv' | 'json'
    user_id?: string
    action?: string
    status_code?: number
    start_date?: string
    end_date?: string
    limit?: number
  } = {}) {
    return service.get('/admin/audit-logs/export', {
      params: { format: 'csv', ...params },
      responseType: 'blob',
    })
  },

  /** 租户列表（仅平台管理员） */
  listTenants(params: { page?: number; page_size?: number } = {}) {
    return service.get('/admin/tenants', { params })
  },

  /** 新建租户 */
  createTenant(payload: {
    name: string
    code: string
    plan?: string
    max_users?: number
    contact_email?: string
    remark?: string
  }) {
    return service.post('/admin/tenants', payload)
  },

  /** 用户列表（默认本租户） */
  listUsers(params: { page?: number; page_size?: number; search?: string } = {}) {
    return service.get('/admin/users', { params })
  },

  /** 把用户划归租户 */
  assignUserTenant(userId: string, tenantId: string) {
    return service.put(`/admin/users/${userId}/tenant`, { tenant_id: tenantId })
  },
}

export default adminApi
