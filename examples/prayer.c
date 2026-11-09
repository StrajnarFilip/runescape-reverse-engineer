/* Synthetic timing example. No client is opened and no input is sent. */
#include <stdio.h>
#include "rs3/prayer.h"
int main(void) {
    rs3_prayer_config config={sizeof config,RS3_PRAYER_SOUL_SPLIT,200,50,100,100,100,0};
    rs3_prayer_planner *planner=NULL;
    if (rs3_prayer_create(&config,&planner)!=RS3_OK) return 1;
    rs3_prayer_frame frame={10000000000ull,10000000000ull,1,1,15000000000ull,
        15,RS3_PRAYER_SOUL_SPLIT,RS3_PRAYER_OVERHEADS,840};
    rs3_prayer_threat attack={1,42,10200000000ull,10200000000ull,
        RS3_PRAYER_MAGIC,RS3_PRAYER_TIMING_VALIDATED};
    rs3_prayer_decision result;
    int32_t code=rs3_prayer_step(planner,&frame,&attack,1,&result,sizeof result);
    if (code==RS3_OK) printf("Synthetic proposal: action=%u prayer=%u reason=%s\n",
        result.action,result.prayer,rs3_prayer_reason_string(result.reason));
    int success=code==RS3_OK && result.action==RS3_PRAYER_SET && result.prayer==RS3_PRAYER_MAGIC;
    rs3_prayer_destroy(planner); return success?0:1;
}
