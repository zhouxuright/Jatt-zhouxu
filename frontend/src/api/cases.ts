import service from './index'

export interface CaseSearchParams {
  query: string
  case_type?: string
  cause_of_action?: string
  court_name?: string
  year_from?: number
  year_to?: number
  top_k?: number
}

export interface CaseItem {
  id: string
  case_number: string
  title: string
  court_name?: string
  case_type?: string
  cause_of_action?: string
  decision_date?: string
  summary?: string
  key_points?: string
  referenced_laws?: string
  tags?: string
  relevance_score: number
}

export interface CaseSearchResponse {
  query: string
  total: number
  results: CaseItem[]
  ai_summary?: string
}

export interface CaseDetail {
  id: string
  case_number: string
  title: string
  court_name?: string
  case_type?: string
  cause_of_action?: string
  decision_date?: string
  parties?: string
  summary?: string
  full_text?: string
  key_points?: string
  referenced_laws?: string
  judgment_result?: string
  tags?: string
  created_at: string
}

export interface CaseCategories {
  case_types: string[]
  causes_of_action: string[]
  courts: string[]
}

export const caseApi = {
  searchCases(params: CaseSearchParams) {
    return service.post<CaseSearchResponse>('/cases/search', params)
  },

  getCaseDetail(caseId: string) {
    return service.get<CaseDetail>(`/cases/${caseId}`)
  },

  getCategories() {
    return service.get<CaseCategories>('/cases/categories')
  },
}
