import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { api } from "../api/client";
import type { SiteSettings } from "../api/types";

const DEFAULT_BRAND: SiteSettings = {
  site_name: "Safety Detect",
  logo_url: null,
};

interface BrandContextValue {
  siteName: string;
  logoUrl: string | null;
  refresh: () => Promise<void>;
}

const BrandContext = createContext<BrandContextValue | null>(null);

export function BrandProvider({ children }: { children: ReactNode }) {
  const [brand, setBrand] = useState<SiteSettings>(DEFAULT_BRAND);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const settings = await api.request<SiteSettings>("/api/settings/site");
      setBrand({
        site_name: settings.site_name.trim() || DEFAULT_BRAND.site_name,
        logo_url: settings.logo_url || null,
      });
    } catch {
      setBrand(DEFAULT_BRAND);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    document.title = brand.site_name;
  }, [brand.site_name]);

  const value = useMemo(
    () => ({ siteName: brand.site_name, logoUrl: brand.logo_url, refresh }),
    [brand, refresh],
  );

  if (loading) {
    return <div role="status">正在加载站点配置</div>;
  }
  return <BrandContext.Provider value={value}>{children}</BrandContext.Provider>;
}

export function useBrand(): BrandContextValue {
  const value = useContext(BrandContext);
  if (!value) {
    throw new Error("useBrand must be used within BrandProvider");
  }
  return value;
}
