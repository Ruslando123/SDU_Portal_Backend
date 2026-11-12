# Known limitations

| Area | Status | Release impact |
| --- | --- | --- |
| Student Portal, disposable demo and private monitoring | Independently Tested | Suitable for a synthetic local demonstration |
| Protected Modal administration | Independently Tested | Private single-process operator mode. Enabled launch requires server configuration, an authenticated admin, CSRF and confirmation |
| Intermittent HTTP 502 on standard Modal Sandbox Connect | Unresolved | A reproduced 502 had no matching gateway ingress. The exact upstream component remains unknown |
| 200-VU cloud gate | Transport-specific | The alternate encrypted tunnel has one pass per profile. Connect reproduced the intermittent read 502, and mixed was not tested there |
| 500-VU cloud gate | Mixed failed | Read passed once with pool 24. Mixed failed HTTP and SQL reconciliation with pool 24 and again with a prefilled pool 32 |
| 1,000, 2,500 and 5,000 cloud VUs | Not Tested | Escalation requires read and mixed to pass at the preceding tier |
| Open-model read throughput | 160 reads/s reproduced | Two 120-second windows passed. The 240 reads/s result passed once and failed on repeat. The production maximum remains unverified |
| Cloud host-wide metrics | Not Tested | Java process CPU/RSS and selected JDBC and PostgreSQL metrics are available when sampled. Unavailable whole-container and host evidence is shown as No Data |
| Deployment | Not Tested | Synthetic fixtures and loopback deployment are verified. Real SDU identity, production traffic and production infrastructure are out of scope |

No production readiness or university-wide capacity claim is made. The demo uses the retained benchmark evidence and a separate PostgreSQL fixture. [Evidence](bottleneck-analysis-final.md) · [Verified envelope](performance-capacity-report.md)
