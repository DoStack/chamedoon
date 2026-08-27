export type Locale = "en" | "fa";

export type User = {
  id: number;
  telegram_user_id: number;
  telegram_username: string | null;
  first_name: string;
  last_name: string | null;
  is_active: boolean;
};

export type Category = {
  code: string;
  name_en: string;
  name_fa: string;
  sort_order: number;
};

export type City = {
  slug: string;
  name_en: string;
  name_fa: string;
};

export type Country = {
  code: string;
  name_en: string;
  name_fa: string;
  cities: City[];
};

export type Locations = {
  countries: Country[];
};

export type RequestType = "DEMAND" | "SUPPLY";
export type RequestStatus = "ACTIVE" | "CANCELLED" | "EXPIRED" | "COMPLETED";

export type ItemRequest = {
  id: number;
  type: RequestType;
  origin_country: string;
  origin_city: string;
  destination_country: string;
  destination_city: string;
  date_from: string;
  date_to: string;
  weight_kg: string | null;
  capacity_kg: string | null;
  item_category_codes: string[];
  excluded_category_codes: string[];
  excluded_other_text: string;
  description: string;
  status: RequestStatus;
  expires_at: string;
  match_count: number;
  created_at: string;
  updated_at: string;
};

export type MatchStatus =
  | "SUGGESTED"
  | "ACCEPTED_BY_SUPPLY"
  | "ACCEPTED_BY_DEMAND"
  | "CONNECTED"
  | "REJECTED"
  | "EXPIRED"
  | "COMPLETED";

export type RequestSummary = {
  id: number;
  type: RequestType;
  origin_country: string;
  origin_city: string;
  destination_country: string;
  destination_city: string;
  date_from: string;
  date_to: string;
  weight_kg: string | null;
  capacity_kg: string | null;
  item_category_codes: string[];
  status: RequestStatus;
};

export type Counterpart = {
  first_name: string;
  telegram_username: string | null;
  telegram_user_id: number;
  telegram_url: string;
  draft?: string;
};

export type Match = {
  id: number;
  score: string;
  score_label: "STRONG" | "POSSIBLE" | "WEAK";
  status: MatchStatus;
  demand_request: RequestSummary;
  supply_request: RequestSummary;
  my_role: "demand" | "supply";
  counterpart: Counterpart | null;
  created_at: string;
  updated_at: string;
};

export type OpenRequest = RequestSummary & {
  owner_first_name: string;
  excluded_category_codes: string[];
  description: string;
};

export type ApiErrorBody = Record<string, unknown>;
