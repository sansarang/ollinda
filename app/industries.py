"""
업종 프로필 — 업종별 콘텐츠 특화 데이터.
캡션 톤/해시태그/콘텐츠 앵글/촬영 가이드/법적 주의를 한 곳에서 관리.
새 업종 추가 = PROFILES 에 항목 하나 추가하면 끝.
tenant.industry(자유 문자열)는 resolve_industry()로 별칭 매칭 → 프로필 결정.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IndustryProfile:
    key: str                       # 슬러그
    name: str                      # 표시명
    aliases: list[str]             # 매칭용 별칭/키워드
    tone: str                      # 캡션 톤 지시문
    hashtag_seeds: list[str]       # 기본 해시태그(지역/메뉴는 동적 추가)
    content_angles: list[str]      # "무엇을 찍어 보낼지" 소재 앵글
    photo_guide: list[str]         # 사장님용 촬영 가이드
    cta: str                       # 행동유도 스타일
    persona: str = ""              # 업종 페르소나(말투/표현) — 강하게 적용
    cautions: list[str] = field(default_factory=list)  # 법적/표현 주의
    pain_points: str = ""          # 고객이 겪는 진짜 고민(공감 훅·PAS 오프닝용)
    trust_signals: str = ""        # 구매/방문 결정 요소(신뢰 신호) — 콘텐츠에 녹임
    example_copy: str = ""         # few-shot: 잘 쓴 문구 예시(LLM이 톤·구조 모방)
    viral_hooks: str = ""          # 바이럴 훅 앵글(업종별 '잘 터지는' 오프닝 — 사실 기반, 과장 금지)


PROFILES: dict[str, IndustryProfile] = {
    "tinting": IndustryProfile(
        key="tinting", name="썬팅업체",
        aliases=["썬팅", "선팅", "틴팅", "자동차필름", "윈도우필름", "열차단"],
        tone="전문성과 시공 퀄리티를 강조하고, 열차단·자외선 차단 효과와 하자보증을 신뢰감 있게 전달.",
        hashtag_seeds=["#썬팅", "#자동차썬팅", "#썬팅필름", "#열차단필름", "#신차썬팅", "#틴팅"],
        content_angles=["시공 전/후 비교", "열차단 등급·데이터", "신차 패키지", "차종별 시공사례", "하자보증·애프터"],
        photo_guide=["시공 전/후 같은 각도·조명", "차량+필름 등급 표기", "디테일(엣지 마감) 클로즈업"],
        cta="견적·시공 예약 문의 유도",
        persona=("기술력과 시공 디테일로 신뢰를 준다. Before/After를 강조하고 보증기간·차종별 추천을 "
                 "구체적으로. 과장보다 '이렇게 시공했습니다' 식 팩트와 마감 디테일로 말한다."),
        cautions=["효과는 과장 없이(체감/등급 기준)", "타사 비방 금지"],
        pain_points="여름 차 안 찜통·앞유리 눈부심, 자외선에 피부·대시보드 손상, 사생활 노출, 몇 달 만에 벗겨지는 싸구려 필름",
        trust_signals="열차단 등급 데이터, 시공 전/후 실측, 하자보증 기간, 정품 필름, 차종별 시공 경험, 기포 없는 마감",
        example_copy="한여름 신호 대기만 해도 팔뚝이 익죠? 오늘 시공한 제네시스 G80은 열차단 1등급으로 잡았어요. 필름 붙이기 전 유리 먼지 한 톨까지 물세척으로 잡아야 기포가 안 생깁니다. 시공 후 안이 확 시원해진 거 직접 보여드릴게요.",
    ),
    "usedcar": IndustryProfile(
        key="usedcar", name="중고차판매",
        aliases=["중고차", "중고차매매", "중고자동차", "자동차매매", "카매니저", "중고차딜러",
                 "딜러", "중고차상사", "중고차매매단지", "중고매매"],
        tone="투명성·신뢰 최우선 + '손님 편인 정직한 전문가' 톤. 실매물·성능점검·사고이력을 먼저 솔직하게. 과장·허위·미끼 절대 금지.",
        hashtag_seeds=["#중고차", "#중고차매매", "#실매물", "#무사고", "#중고차시세", "#정직한딜러", "#중고차추천"],
        content_angles=[
            "신규 입고 매물(외관4방향·실내·엔진룸·주행거리)",
            "성능점검기록부·사고이력 공개 인증",
            "요즘 시세 정보('이 차 얼마')",
            "고객 인도 후기(판매 인증)",
            "'이런 차 찾으시면 연락'(수요 매칭)",
            "딜러가 안 알려주는 것·호구 방지(교육)",
            "할부/리스/보증 안내",
        ],
        photo_guide=["외관 4방향+실내+계기판(주행거리)", "엔진룸·하부·타이어 디테일", "번호판/연락처 개인정보 가림", "흠집도 솔직히"],
        cta="매물 문의·시승 예약 유도 + 딜러 연락처",
        persona=("'손님 편인 정직한 전문가' 딜러. 1인칭('제가 직접 검수한 차', '상태 솔직히 말씀드리면'). "
                 "중고차 손님의 사기·호구 공포를 정면으로 공감하고, 성능점검·사고이력·흠집을 '먼저' 투명하게 공개해 신뢰를 쌓는다. "
                 "가끔 업계 비밀을 살짝 알려주는 교육형('이거 모르면 호구'). 과장·미끼 절대 금지. 소개·재구매를 자연스럽게 유도."),
        cautions=["허위·미끼매물 금지(자동차관리법·표시광고법)", "'완전무사고' 남발 금지 — 사고이력 정확히",
                  "주행거리 정확", "가격·수수료 명확히"],
        pain_points="사고차·침수차일까 불안, 시세보다 비싼 건 아닐까, 허위·미끼매물에 헛걸음, 계약 후 숨은 하자, 딜러가 뭔가 숨길까",
        trust_signals="성능점검기록부, 무사고·실주행거리, 실매물 사진(흠집·엔진룸 포함), 사고이력 조회, 보증·환불 조건, 딜러 실명·소속 상사·연락처, 고객 인도 후기",
        example_copy="'사고차 아니에요?' 중고차 손님 열에 아홉이 이거부터 물어요. 솔직히 말씀드릴게요. 이 그랜저 IG, 성능점검부 그대로 무사고에 실주행 3만km입니다. 운전석 문콕 하나 있는데 사진에 다 찍어놨어요 — 숨기고 팔 이유가 없거든요. 감추면 결국 손님이 등 돌립니다. 오셔서 성능점검부·주행거리·흠집까지 직접 확인하고 결정하세요.",
        viral_hooks=("[중고차 바이럴 훅 — 사진·매물에 맞는 1개로 오프닝(반드시 사실 기반, 과장·허위 금지)]\n"
                     "① 폭로형: '중고차 딜러가 안 알려주는 것'\n"
                     "② 반전 솔직: '이 차 이런 단점 있어요, 근데 추천하는 이유는'\n"
                     "③ 교육/보호: '호구 안 잡히는 성능점검부 보는 법'\n"
                     "④ 투명: '시세보다 싼/비싼 이유 다 공개'\n"
                     "⑤ 반대 조언: 'OO 살 바엔 이 차 보세요'\n"
                     "⑥ 비교: '천만원대 vs 삼천만원대 뭐가 다를까'\n"
                     "⑦ 딜러 신뢰: '제가 산다면 이 차 삽니다'\n"
                     "⑧ 고객 인도 후기 / ⑨ 경매장·매입 비하인드"),
    ),
    "clothing": IndustryProfile(
        key="clothing", name="옷가게",
        aliases=["옷가게", "의류", "의류매장", "패션", "boutique", "편집샵"],
        tone="트렌디하고 감성적인 톤. 코디 제안과 착용감으로 구매욕을 자극.",
        hashtag_seeds=["#데일리룩", "#코디", "#신상", "#ootd", "#패션스타그램"],
        content_angles=["신상 입고", "코디 제안(상하의 매치)", "착용샷", "시즌 세일"],
        photo_guide=["착용샷+디테일컷", "자연광에서 색감 살리기", "전신+소재 클로즈업"],
        cta="방문·사이즈/재고 문의 유도",
        persona=("코디 제안형 말투. '이 옷 입으면 이런 느낌', 계절감, 체형·상황별 추천을 친한 패션 친구처럼. "
                 "스타일링 팁을 곁들여 구매 상상을 자극한다."),
        cautions=["원산지·소재 표기 정확", "타브랜드 이미지 무단사용 금지"],
        pain_points="온라인은 사이즈·색감 실패, 어떻게 코디할지 막막, 남들과 겹치는 옷, 실물과 다를까 걱정",
        trust_signals="실착 사이즈감(키·몸무게 기준), 소재·핏 설명, 코디 제안, 자연광 실물 색감",
        example_copy="이 니트 하나로 출근룩도 데이트룩도 됩니다. 168cm 55 기준 살짝 여유 있게 떨어지고, 슬랙스엔 넣어 입고 청바지엔 빼 입으면 느낌 완전 달라요. 색은 실물이 사진보다 차분합니다.",
    ),
    "hair": IndustryProfile(
        key="hair", name="미용실",
        aliases=["미용실", "헤어샵", "헤어", "살롱", "펌", "염색", "커트", "미용사"],
        tone="친근하고 트렌디한 톤. 시술 전/후 변화로 신뢰를 주고 예약을 유도.",
        hashtag_seeds=["#헤어스타일", "#펌", "#염색", "#헤어", "#미용실추천"],
        content_angles=["시술 전/후", "신규 스타일·시술 메뉴", "이벤트·할인", "디자이너 소개"],
        photo_guide=["시술 전/후 같은 조명·각도", "정면+측면+뒷모습", "디테일(컬·컬러) 클로즈업"],
        cta="예약·상담 유도",
        persona=("트렌디하고 친근하게. 시술 전/후 변화를 또렷이 보여주고 시술명·홈케어 팁을 구체적으로. "
                 "'이런 분께 추천' 식으로 타겟을 콕 집는다."),
        cautions=["전후 사진은 동일 인물 동의", "효과 과장 금지"],
        pain_points="실패하면 몇 달 스트레스, 내 얼굴형에 뭐가 어울릴지 모름, 손상·탈색 걱정, 원하는 스타일 설명 어려움",
        trust_signals="시술 전/후 동일 인물, 얼굴형·모질별 추천, 홈케어 팁, 디자이너 이력, 사용 제품",
        example_copy="얼굴 커 보일까 걱정하셨는데, 레이어드컷으로 얼굴 라인 살렸어요. 애쉬브라운은 노랑기 눌러주는 톤이라 물 빠져도 예쁘고 관리 편합니다. 이런 분께 특히 추천드려요.",
    ),
    "restaurant": IndustryProfile(
        key="restaurant", name="음식점",
        aliases=["음식점", "식당", "맛집", "레스토랑", "고깃집", "한식", "분식"],
        tone="식욕을 자극하는 생생한 묘사. 시그니처 메뉴와 분위기를 매력적으로.",
        hashtag_seeds=["#맛집", "#맛스타그램", "#먹스타그램", "#존맛탱", "#맛집추천"],
        content_angles=["시그니처 메뉴", "신메뉴·점심특선", "단체/예약 안내", "매장 분위기"],
        photo_guide=["음식 클로즈업+김/소스 강조", "자연광·접시 정돈", "메뉴+테이블 세팅"],
        cta="예약·방문·포장 문의 유도",
        persona=("감성과 실용의 균형. 맛 묘사는 생생하게(식감·향), 동시에 가성비·혼밥·단체·예약 같은 실질 정보를 "
                 "함께. 솔직한 '진짜 맛있어서 추천' 톤."),
        cautions=["원산지 표시", "위생/효능 과장 금지"],
        pain_points="맛집인지 실패 걱정, 웨이팅·가성비, 뭘 시킬지 모름, 단체·주차 되나",
        trust_signals="시그니처 메뉴, 신선한 재료·원산지, 실제 손님 후기, 가격·영업시간·주차, 혼밥/단체 가능",
        example_copy="점심에 뭐 먹을지 고민이면 이거예요. 오늘 나온 김치찌개정식, 묵은지 푹 끓여서 국물이 진하고 깊어요. 평일 12~2시 8천원, 밥·계란찜 리필 됩니다. 직장인분들 딱이에요.",
    ),
    "cafe": IndustryProfile(
        key="cafe", name="카페",
        aliases=["카페", "커피", "디저트카페", "베이커리", "브런치"],
        tone="감성적이고 따뜻한 톤. 시그니처 음료·디저트와 공간 분위기를 강조.",
        hashtag_seeds=["#카페추천", "#감성카페", "#디저트", "#카페스타그램", "#커피맛집"],
        content_angles=["시그니처 음료", "신메뉴 디저트", "공간·인테리어", "이벤트·쿠폰"],
        photo_guide=["음료+공간 함께", "자연광 감성컷", "디저트 클로즈업+소품"],
        cta="방문·예약·신메뉴 안내 유도",
        persona=("감성과 실용의 균형. 분위기(데이트·혼카페·작업하기 좋은)와 시그니처 메뉴를 함께 전한다. "
                 "따뜻하고 진솔한 동네 단골 톤."),
        cautions=["타카페 메뉴/사진 도용 금지", "알레르기 정보 정확"],
        pain_points="공부·작업할 자리 있나, 인스타 감성인가, 디저트 맛있나, 너무 붐비진 않나, 커피 맛있나",
        trust_signals="시그니처 음료·원두, 좌석·콘센트·와이파이, 공간 무드, 수제 디저트, 조용함/뷰",
        example_copy="노트북 하기 좋은 자리 찾으신다면 여기예요. 신메뉴 흑임자 라떼는 고소하고 안 달아서 오래 앉아있기 딱이에요. 창가 콘센트 자리 넉넉하고, 오후엔 햇살 들어와서 감성 사진도 잘 나옵니다.",
    ),
    "gym": IndustryProfile(
        key="gym", name="헬스장",
        aliases=["헬스장", "헬스", "피트니스", "gym", "웨이트", "pt", "퍼스널", "퍼스널트레이닝"],
        tone="변화·동기부여 중심. 회원 성과와 시설·PT 전문성을 신뢰감 있게.",
        hashtag_seeds=["#헬스장", "#헬스", "#PT", "#다이어트", "#헬스타그램", "#운동"],
        content_angles=["회원 전/후 변화", "PT 프로그램·자세 교정", "시설·기구 소개", "등록 혜택·이벤트"],
        photo_guide=["기구·시설 밝게", "전/후 동일 각도(동의)", "PT 세션 현장감"],
        cta="무료 체험·PT 상담 유도",
        persona="동기부여형. '혼자선 어렵다, 옆에서 잡아준다'는 톤. 성과는 구체적 사례로, 무리한 보장은 금지.",
        cautions=["효과·기간 과장 금지", "의료·처방 행위 아님"],
        pain_points="작심삼일 걱정, 혼자선 방법·자세 모름, 눈치 보임, 비싼 PT 값어치 할까, 다칠까 두려움",
        trust_signals="회원 실제 변화 사례, 트레이너 자격·경력, 시설·기구, 체계적 프로그램, 무료 체험",
        example_copy="'작심삼일'이 무섭죠? 혼자 하면 그래요. 3개월 만에 -8kg 만드신 회원님도 처음엔 스쿼트 자세부터 몰랐어요. PT는 살 빼주는 게 아니라 '혼자 못 하는 걸 옆에서 잡아주는 것'입니다. 무료 체험부터 편하게 오세요.",
    ),
    "pilates": IndustryProfile(
        key="pilates", name="필라테스",
        aliases=["필라테스", "요가", "pilates", "yoga", "리포머", "체형교정", "소도구"],
        tone="자세교정·라인·힐링 중심. 차분하고 전문적.",
        hashtag_seeds=["#필라테스", "#요가", "#체형교정", "#다이어트", "#필라테스추천"],
        content_angles=["체형 전/후", "기구/소도구 수업", "강사 소개", "체험·등록 혜택"],
        photo_guide=["동작 라인 예쁘게", "밝은 스튜디오", "전/후 자세(동의)"],
        cta="체험 수업·등록 상담 유도",
        persona="차분·전문적. 자세교정·통증완화·라인 개선을 구체적으로. 의료효능 과장 금지.",
        cautions=["치료·의료효능 과장 금지", "통증은 전문의 상담 권고"],
        pain_points="거북목·라운드숄더·골반틀어짐, 운동 무서움, 초보·나이 걱정, 살만 빠질까 자세도 좋아질까",
        trust_signals="체형 전/후, 강사 자격, 소수정예·1:1, 리포머 기구, 체험 수업",
        example_copy="하루 종일 앉아있어서 어깨가 앞으로 말리셨죠? 8주 하신 회원님, 굽은 등이 확 펴졌어요. 필라테스는 '살 빼기'보다 '틀어진 몸을 제자리로'예요. 처음이면 1:1 체험부터 편하게 시작하세요.",
    ),
    "clinic": IndustryProfile(
        key="clinic", name="병원·의원",
        aliases=["병원", "의원", "내과", "정형외과", "이비인후과", "가정의학과", "통증", "한의원", "한방"],
        tone="신뢰·전문성 최우선. 정확한 정보, 과장·확정 진단 표현 금지.",
        hashtag_seeds=["#병원", "#의원", "#건강정보", "#진료안내"],
        content_angles=["진료 분야·장비 안내", "건강 정보(예방·관리)", "진료 시간·예약 안내", "의료진 소개"],
        photo_guide=["청결한 진료·대기 공간", "장비(개인정보 없이)", "밝고 신뢰감 있게"],
        cta="진료 예약·문의 유도",
        persona="차분하고 전문적. 건강 정보를 쉽게 풀되 과장·단정 금지. '이런 증상이면 진료 권장' 톤.",
        cautions=["의료광고법 준수(효과 보장·최고·유일 금지)", "확정 진단·치료 효과 단정 금지", "환자 사진·후기 동의·규정 준수"],
        pain_points="증상이 뭔지 불안, 어느 병원 가야 할지, 대기·과잉진료 걱정, 설명 잘 안 해줄까",
        trust_signals="진료 분야·장비, 의료진 경력, 진료시간·예약, 쉬운 설명, 위치·주차",
        example_copy="아침에 손가락이 뻣뻣하고 붓는 느낌, 그냥 두면 안 됩니다. 이런 증상은 조기에 원인을 찾는 게 중요해요. 저희는 검사부터 결과 설명까지 충분히 시간 들여 봐드립니다. 증상 있으시면 예약 후 방문하세요.",
    ),
    "dental": IndustryProfile(
        key="dental", name="치과",
        aliases=["치과", "임플란트", "교정", "치아", "덴탈", "스케일링"],
        tone="신뢰·정확·통증 배려. 과장 금지, 과정 투명하게.",
        hashtag_seeds=["#치과", "#임플란트", "#치아교정", "#치과추천", "#스케일링"],
        content_angles=["진료 과정·장비", "치아 관리 정보", "비용·상담 안내", "의료진 소개"],
        photo_guide=["청결한 진료실·장비", "환자 얼굴 노출 주의", "밝고 위생적으로"],
        cta="상담·예약 유도",
        persona="꼼꼼하고 안심시키는 톤. 통증·비용 걱정을 미리 해소. 과장·확정 금지.",
        cautions=["의료광고법 준수(최고·무통·100% 금지)", "치료 효과 단정 금지", "환자 사진 동의"],
        pain_points="아플까 무섭다, 비용 부담·바가지 걱정, 과잉진료 아닐까, 오래 걸릴까",
        trust_signals="진료 과정 설명, 장비·재료, 비용 투명, 의료진 경력, 사후관리",
        example_copy="치과 오는 게 겁나시죠? 저희는 왜 이 치료가 필요한지, 비용이 왜 이런지 먼저 다 설명드립니다. 안 해도 되는 치료는 안 한다고 말씀드려요. 통증도 최대한 배려하니 상담부터 편하게 오세요.",
    ),
    "skincare": IndustryProfile(
        key="skincare", name="피부관리·에스테틱",
        aliases=["피부관리", "에스테틱", "피부과", "왁싱", "관리실", "스킨케어", "테라피"],
        tone="변화·힐링·자기관리 감성. 효과 과장 금지.",
        hashtag_seeds=["#피부관리", "#에스테틱", "#왁싱", "#피부관리실", "#관리후기"],
        content_angles=["관리 전/후", "프로그램·시술 소개", "홈케어 팁", "이벤트·첫 방문 혜택"],
        photo_guide=["전/후 동일 조명(동의)", "청결한 관리실", "제품·디테일 클로즈업"],
        cta="예약·상담 유도",
        persona="따뜻하고 전문적. 피부 고민 공감 + 관리 과정을 구체적으로. 의료효능 과장 금지.",
        cautions=["의료행위·치료효과 표현 금지", "전후 사진 동의", "효과 개인차 명시"],
        pain_points="트러블·모공·탄력 고민, 효과 있을까, 비쌀까, 나만 관리 안 하나",
        trust_signals="관리 전/후, 사용 제품·기기, 관리사 경력, 홈케어 안내, 첫 방문 혜택",
        example_copy="자고 일어나면 얼굴이 푸석하고 모공이 도드라지시죠? 오늘 관리받으신 분, 각질 정리하고 수분 채우니 결이 확 매끈해졌어요. 집에선 이 순서로만 관리해보세요. 첫 방문 상담은 부담 없이 오세요.",
    ),
    "academy": IndustryProfile(
        key="academy", name="학원·교습소",
        aliases=["학원", "교습소", "공부방", "과외", "영어학원", "수학학원", "입시", "보습"],
        tone="성과·신뢰·학부모 안심. 과장 합격률 금지.",
        hashtag_seeds=["#학원", "#입시", "#공부", "#학원추천", "#내신"],
        content_angles=["학습 프로그램·커리큘럼", "성적 향상 사례", "강사·관리 시스템", "설명회·등록 안내"],
        photo_guide=["밝은 강의실·자습실", "학생 얼굴 노출 주의", "교재·판서"],
        cta="상담·레벨테스트 예약 유도",
        persona="믿음직하고 따뜻한 톤. 관리 시스템·성적 향상을 구체적으로. 과장 합격 보장 금지.",
        cautions=["합격률·성적 과장 금지", "학생 사진·정보 동의", "환불 규정 안내"],
        pain_points="우리 애 성적 오를까, 관리 제대로 될까, 이 학원 맞을까, 학부모 소통 잘 될까",
        trust_signals="커리큘럼·진도관리, 성적 향상 사례, 강사 이력, 개별 관리·피드백, 상담·설명회",
        example_copy="'우리 애는 왜 학원 다녀도 안 오를까' 고민이시죠? 성적은 '많이'가 아니라 '아는 것과 모르는 것을 나누는 것'에서 시작합니다. 저희는 매주 개별 오답을 잡아 학부모님께도 공유드려요. 레벨테스트부터 상담해보세요.",
    ),
    "realestate": IndustryProfile(
        key="realestate", name="부동산·공인중개사",
        aliases=["부동산", "공인중개사", "중개", "매매", "전세", "월세", "상가", "원룸"],
        tone="투명·신뢰. 허위매물 절대 금지, 정확한 정보.",
        hashtag_seeds=["#부동산", "#매매", "#전세", "#월세", "#상가임대"],
        content_angles=["신규 매물 소개", "동네·입지 정보", "시세·계약 팁", "실매물 후기"],
        photo_guide=["매물 실내·채광", "주소·호수 개인정보 주의", "입지(교통·편의)"],
        cta="매물 문의·방문 상담 유도",
        persona="솔직하고 전문적. '실매물만, 단점도 말씀드린다' 톤. 지역 입지를 잘 아는 동네 전문가.",
        cautions=["허위·과장 매물 금지(공인중개사법)", "확정 수익·시세 단정 금지", "개인정보 보호"],
        pain_points="허위매물에 헛걸음, 시세보다 비싼가, 계약 사고 걱정, 동네 정보 부족",
        trust_signals="실매물 사진·주소 확인, 시세 근거, 계약·권리 확인, 동네 입지 지식, 단점도 고지",
        example_copy="'사진이랑 다른 매물' 때문에 헛걸음하신 적 있으시죠? 이 원룸은 오늘 직접 찍은 실매물입니다. 채광 좋고 관리비 저렴한데, 대신 엘리베이터 없는 3층이에요. 단점까지 말씀드리니 편하게 보러 오세요.",
    ),
    "interior": IndustryProfile(
        key="interior", name="인테리어·리모델링",
        aliases=["인테리어", "리모델링", "시공", "집수리", "도배", "타일", "욕실", "주방"],
        tone="시공 퀄리티·과정 투명. 견적·하자보증 신뢰감 있게.",
        hashtag_seeds=["#인테리어", "#리모델링", "#시공", "#집스타그램", "#인테리어추천"],
        content_angles=["시공 전/후", "시공 과정·자재", "견적·상담 안내", "고객 후기"],
        photo_guide=["전/후 같은 각도", "디테일 마감 클로즈업", "자재·과정"],
        cta="견적·상담 문의 유도",
        persona="꼼꼼하고 신뢰감. 과정·자재·하자보증을 구체적으로. '이렇게 시공했습니다' 팩트 톤.",
        cautions=["견적·기간 과장 금지", "하자보증 조건 명확", "타사 비방 금지"],
        pain_points="견적 바가지 걱정, 하자·A/S, 공사 중 소통, 마감 퀄리티 믿을 수 있나",
        trust_signals="시공 전/후, 자재·과정 공개, 견적 투명, 하자보증, 실제 후기",
        example_copy="'견적서 받아보면 왜 이렇게 다를까' 하시죠? 오늘 끝낸 욕실, 방수부터 타일 줄눈까지 과정 다 찍어놨어요. 자재 등급·수량 그대로 견적에 넣습니다. 하자보증도 서면으로 드리니 견적부터 편하게 문의 주세요.",
    ),
    "florist": IndustryProfile(
        key="florist", name="꽃집·플라워샵",
        aliases=["꽃집", "플라워", "화훼", "플로리스트", "꽃다발", "화분", "화환"],
        tone="감성·따뜻함. 상황별 추천과 신선도.",
        hashtag_seeds=["#꽃집", "#꽃다발", "#플라워샵", "#꽃선물", "#플로리스트"],
        content_angles=["시즌 꽃·신상", "상황별 추천(기념일·개업)", "당일 픽업·배송", "제작 과정"],
        photo_guide=["자연광 색감", "꽃 클로즈업+전체", "포장 디테일"],
        cta="주문·픽업/배송 문의 유도",
        persona="따뜻하고 센스 있는 톤. '이 상황엔 이 꽃' 상황별 제안. 신선도·당일 제작을 강조.",
        cautions=["꽃 사진 도용 금지", "가격·구성 정확"],
        pain_points="무슨 꽃을 줘야 할지 모름, 시들까 걱정, 예산 맞을까, 당일 되나",
        trust_signals="당일 입고 신선 꽃, 상황별 추천, 예산 맞춤 제작, 당일 픽업·배송, 실물 사진",
        example_copy="기념일인데 무슨 꽃 줄지 막막하시죠? 프러포즈면 이 조합, 부모님 생신이면 이 색이 좋아요. 오늘 들어온 꽃으로 예산 안에서 제일 예쁘게 만들어드립니다. 당일 픽업·배송 되니 편하게 주문 주세요.",
    ),
    "nail": IndustryProfile(
        key="nail", name="네일샵",
        aliases=["네일", "네일샵", "네일아트", "젤네일", "속눈썹", "왁싱네일"],
        tone="트렌디·감성. 디자인과 손기술, 지속력.",
        hashtag_seeds=["#네일", "#네일아트", "#젤네일", "#네일샵", "#네일스타그램"],
        content_angles=["시즌 디자인", "시술 과정·아트", "지속력·케어 팁", "이벤트·첫 방문"],
        photo_guide=["손 디자인 클로즈업", "자연광 색감", "과정 컷"],
        cta="예약·디자인 상담 유도",
        persona="친근하고 트렌디. 디자인 제안 + 지속력·꼼꼼함 강조. '이런 분께 추천' 톤.",
        cautions=["시술 사진 도용 금지", "위생·소독 안내"],
        pain_points="금방 들뜨고 깨질까, 내 손톱에 어울릴 디자인, 가격, 오래 걸릴까",
        trust_signals="디자인 포트폴리오, 지속력(2~3주), 위생·소독, 꼼꼼한 마감, 첫 방문 혜택",
        example_copy="젤네일 며칠 만에 들뜬 적 있으시죠? 큐티클 정리랑 베이스 꼼꼼히 해야 3주는 갑니다. 이번 시즌은 이 뮤트톤이 손 하얘 보여서 인기예요. 원하는 느낌만 말씀하시면 손 모양 맞춰 추천드릴게요.",
    ),
    "pension": IndustryProfile(
        key="pension", name="펜션·숙박",
        aliases=["펜션", "숙박", "게스트하우스", "풀빌라", "독채", "민박", "스테이"],
        tone="감성·힐링. 공간·뷰·부대시설 매력적으로.",
        hashtag_seeds=["#펜션", "#풀빌라", "#감성숙소", "#여행", "#독채펜션"],
        content_angles=["객실·뷰", "부대시설(수영장·바비큐)", "주변 여행지", "예약·시즌 혜택"],
        photo_guide=["객실+창밖 뷰", "저녁·조명 무드", "부대시설 활용컷"],
        cta="예약·문의 유도",
        persona="감성적이고 친절한 톤. '여기서 이런 시간을' 상상 자극. 뷰·프라이빗·부대시설 강조.",
        cautions=["시설·사진 실제와 일치", "예약·환불 규정 명확"],
        pain_points="사진과 다를까, 프라이빗한가, 주변에 뭐 있나, 청결한가",
        trust_signals="실제 객실·뷰 사진, 프라이빗 독채, 부대시설, 청결 관리, 주변 명소, 후기",
        example_copy="숙소 사진이랑 실제가 다를까 걱정되시죠? 이 독채는 오늘 찍은 그대로예요. 거실 통창으로 바다가 바로 보이고, 저녁엔 프라이빗 바비큐 존에서 노을 보며 구워 드실 수 있어요. 주말은 빨리 마감되니 서둘러 예약하세요.",
    ),
    "pet": IndustryProfile(
        key="pet", name="반려동물·애견",
        aliases=["애견", "반려동물", "애견미용", "동물병원", "펫샵", "강아지", "고양이", "펫"],
        tone="따뜻·신뢰. 반려인 마음에 공감.",
        hashtag_seeds=["#애견미용", "#반려견", "#강아지", "#펫스타그램", "#동물병원"],
        content_angles=["미용 전/후", "케어·건강 팁", "서비스·가격 안내", "귀여운 손님(동의)"],
        photo_guide=["미용 전/후", "안전·청결한 공간", "아이 얼굴 클로즈업"],
        cta="예약·상담 유도",
        persona="따뜻하고 세심한 톤. '내 새끼처럼 케어' 반려인 공감. 안전·위생 강조.",
        cautions=["의료·치료 효과 과장 금지(동물병원)", "손님 반려동물 사진 동의"],
        pain_points="우리 아이 스트레스 받을까, 안전한가, 위생·전문성, 예민한 아이도 되나",
        trust_signals="미용 전/후, 안전·1:1 케어, 위생 관리, 미용사 경력, 예민한 아이 경험",
        example_copy="미용 맡기면 우리 아이 스트레스 받을까 걱정되시죠? 오늘 온 말티즈, 처음엔 덜덜 떨었는데 천천히 달래가며 했어요. 겁 많은 아이일수록 서두르지 않습니다. 아이 성향 미리 말씀해주시면 맞춰서 케어할게요.",
    ),
    "laundry": IndustryProfile(
        key="laundry", name="세탁소·빨래방",
        aliases=["세탁소", "세탁", "빨래방", "코인세탁", "드라이", "런드리"],
        tone="편의·신뢰·꼼꼼함. 실용 정보 중심.",
        hashtag_seeds=["#세탁소", "#코인세탁", "#드라이클리닝", "#빨래방"],
        content_angles=["서비스·가격 안내", "얼룩·관리 팁", "수거·배송 서비스", "특수세탁(패딩·이불)"],
        photo_guide=["깨끗한 매장·설비", "전/후(얼룩 제거)", "가격표"],
        cta="문의·수거 신청 유도",
        persona="친절하고 실용적. 얼룩·특수세탁 노하우를 팁으로. 정직한 가격.",
        cautions=["세탁 사고·배상 규정 안내", "과장 금지"],
        pain_points="얼룩 지워질까, 옷 상할까, 가격·기간, 수거 되나",
        trust_signals="얼룩 전/후, 소재별 관리, 정찰 가격, 수거·배송, 배상 규정",
        example_copy="아끼는 셔츠에 커피 쏟아서 버릴까 고민이셨죠? 오늘 들어온 얼룩, 소재 확인하고 처리하니 깨끗이 빠졌어요. 패딩·이불 같은 특수세탁도 하고 수거·배송도 됩니다. 얼룩 있으면 사진 먼저 보내주세요.",
    ),
    "autorepair": IndustryProfile(
        key="autorepair", name="카센터·정비",
        aliases=["카센터", "정비", "자동차정비", "공업사", "엔진오일", "타이어", "정비소"],
        tone="투명·신뢰. 과잉정비 걱정 해소.",
        hashtag_seeds=["#카센터", "#자동차정비", "#엔진오일", "#타이어", "#정비소"],
        content_angles=["정비 과정·전후", "차량 관리 팁", "점검·비용 안내", "고객 후기"],
        photo_guide=["정비 과정·부품", "번호판 개인정보 주의", "전/후 상태"],
        cta="점검·정비 예약 유도",
        persona="솔직하고 전문적. '필요한 것만, 과잉정비 안 한다' 톤. 과정·비용 투명.",
        cautions=["과잉정비·과장 금지", "부품·비용 정확 고지"],
        pain_points="바가지·과잉정비 걱정, 뭐가 문제인지 모름, 비용, 제대로 고쳐질까",
        trust_signals="정비 과정 공개, 교체 부품 실물, 비용 투명, 필요한 것만, 보증",
        example_copy="'카센터 가면 이것저것 바꾸라 할까 봐' 걱정되시죠? 저는 안 바꿔도 되는 건 안 바꾼다고 말씀드립니다. 오늘 들어온 차, 엔진오일만 갈면 되는데 필터는 아직 괜찮았어요. 교체한 부품은 실물로 다 보여드리니 편하게 오세요.",
    ),
    "photostudio": IndustryProfile(
        key="photostudio", name="사진관·스튜디오",
        aliases=["사진관", "스튜디오", "포토", "프로필", "가족사진", "증명사진", "웨딩촬영"],
        tone="감성·전문. 인생샷·소중한 순간 강조.",
        hashtag_seeds=["#사진관", "#프로필사진", "#가족사진", "#스튜디오", "#증명사진"],
        content_angles=["촬영 결과물(동의)", "컨셉·의상 안내", "보정·인화 서비스", "예약·패키지 안내"],
        photo_guide=["실제 결과물(동의)", "스튜디오·조명", "컨셉·소품"],
        cta="예약·상담 유도",
        persona="따뜻하고 감각적. '가장 예쁜 순간을 남긴다' 톤. 컨셉·보정 퀄리티 강조.",
        cautions=["고객 사진 게시 동의", "보정 범위 사전 안내"],
        pain_points="사진 잘 나올까, 어색할까, 컨셉·의상 고민, 보정 자연스러울까",
        trust_signals="실제 결과물, 컨셉·의상 가이드, 자연스러운 보정, 촬영 경력, 패키지 안내",
        example_copy="카메라 앞에서 어색해서 사진 망칠까 걱정되시죠? 저희는 편하게 대화하면서 자연스러운 표정을 담아요. 오늘 프로필 촬영하신 분, 처음엔 굳어있었는데 결과물 보고 깜짝 놀라셨어요. 컨셉·의상은 미리 같이 정해드립니다.",
    ),
    "optical": IndustryProfile(
        key="optical", name="안경점",
        aliases=["안경점", "안경", "선글라스", "콘택트렌즈", "렌즈", "옵티컬"],
        tone="전문·신뢰. 정확한 시력측정과 얼굴형 추천.",
        hashtag_seeds=["#안경점", "#안경", "#선글라스", "#안경추천", "#렌즈"],
        content_angles=["신상 프레임", "얼굴형별 추천", "시력검사·렌즈 안내", "이벤트·할인"],
        photo_guide=["프레임 디테일", "착용샷(동의)", "밝은 매장"],
        cta="방문·시력검사 예약 유도",
        persona="전문적이고 친근. 얼굴형별 추천 + 정확한 검안 강조. '이런 분께 이 프레임' 톤.",
        cautions=["시력·렌즈 효과 과장 금지", "착용 사진 동의"],
        pain_points="내 얼굴형에 뭐가 어울릴지, 도수 정확할까, 가격, 오래 쓸 수 있나",
        trust_signals="정밀 시력검사, 얼굴형별 추천, 프레임 품질, A/S·조정, 실착 사진",
        example_copy="안경 새로 맞추려는데 뭐가 어울릴지 모르시겠죠? 얼굴이 둥근 편이면 각진 프레임이 라인을 잡아줘요. 저희는 도수도 정밀 검안으로 정확히 맞추고, 코받침·다리 조정도 오래 해드립니다. 편하게 써보러 오세요.",
    ),
}

# 기본(미매칭) 프로필
GENERIC = IndustryProfile(
    key="generic", name="일반 매장",
    aliases=[],
    tone="친근하고 신뢰감 있는 톤으로 매장 방문을 유도.",
    hashtag_seeds=["#동네맛집", "#소상공인", "#가게추천"],
    content_angles=["신규 소식", "이벤트·할인", "매장 분위기"],
    photo_guide=["밝은 자연광", "주제가 분명한 한 컷"],
    cta="방문·문의 유도",
    persona="친근하고 신뢰감 있게. 과장 없이 솔직하게 방문을 유도한다.",
    pain_points="이 가게가 믿을 만한지, 뭐가 특별한지, 가격·위치가 어떤지 궁금함",
    trust_signals="실제 사진·후기, 구체적인 정보(가격·시간·위치), 솔직한 설명",
    example_copy="오늘 이런 소식 전해드려요. 직접 해보니 이런 점이 좋았어요. 궁금하시면 편하게 문의 주세요.",
)

# 시작 업종 (요청: 썬팅/중고차/옷가게/미용실/음식점/카페)
ACTIVE_INDUSTRIES = ["tinting", "usedcar", "clothing", "hair", "restaurant", "cafe"]


# 업종별 작성 예시(업로드 가이드 + '예시 채우기'용). purpose는 폼 select 값과 일치.
EXAMPLES: dict[str, dict] = {
    "tinting": {"note": "신차 제네시스 G80 전면유리 열차단 1등급 시공 완료",
                "purpose": "신상품 홍보", "target": "신차 구매 고객", "extra": "하자보증 5년, 1시간 시공"},
    "usedcar": {"note": "2021 그랜저 IG 무사고 흰색",
                "purpose": "판매 전환", "target": "30~40대", "extra": "급매, 주행 3만km, 보증가능"},
    "clothing": {"note": "가을 신상 니트 입고",
                 "purpose": "방문 유도", "target": "20~30대 여성", "extra": "주말 10% 세일"},
    "hair": {"note": "레이어드컷 + 애쉬브라운 염색 시술",
             "purpose": "방문 유도", "target": "20~30대", "extra": "신규 고객 첫 방문 20%"},
    "restaurant": {"note": "점심특선 김치찌개정식 출시",
                   "purpose": "방문 유도", "target": "직장인", "extra": "평일 12~2시, 8,000원"},
    "cafe": {"note": "신메뉴 흑임자 라떼 출시",
             "purpose": "신상품 홍보", "target": "20~30대", "extra": "오픈 이벤트 10%"},
}
GENERIC_EXAMPLE = {"note": "오늘의 소식 한 줄", "purpose": "방문 유도", "target": "", "extra": "이벤트 내용"}


def example_for(profile: IndustryProfile) -> dict:
    return EXAMPLES.get(profile.key, GENERIC_EXAMPLE)


def industry_brief(p: IndustryProfile) -> str:
    """생성 프롬프트 주입용 — 업종별 고객고민·신뢰요소·few-shot 예시(품질 강화)."""
    parts = []
    if p.pain_points:
        parts.append(f"[이 업종 손님의 진짜 고민(공감 훅·PAS 오프닝에 활용)] {p.pain_points}")
    if p.trust_signals:
        parts.append(f"[신뢰 요소(구매·방문 결정 — 콘텐츠에 자연스럽게 녹여라)] {p.trust_signals}")
    if p.example_copy:
        parts.append("[이 업종 잘 쓴 예시 — 톤·구조만 참고, 내용은 이 가게 실제 정보로 새로 쓸 것(베끼기 금지)]\n"
                     + p.example_copy)
    if p.viral_hooks:
        parts.append(p.viral_hooks)
    if p.cautions:      # 표시광고법·업종 규정 — 전 채널(블로그·쇼츠·X·마켓)에 주의 전달(C3)
        parts.append("[⚠️ 표시광고법·업종 규정 — 반드시 준수, 위반 표현 금지] " + " / ".join(p.cautions))
    return ("\n".join(parts) + "\n") if parts else ""


def _slug(name: str) -> str:
    return (name or "").strip().lower().replace(" ", "_")[:40]


def _profile_from_dict(d: dict) -> IndustryProfile:
    return IndustryProfile(
        key=d.get("key", "custom"), name=d.get("name", "매장"),
        aliases=d.get("aliases", []), tone=d.get("tone", GENERIC.tone),
        hashtag_seeds=d.get("hashtag_seeds", GENERIC.hashtag_seeds),
        content_angles=d.get("content_angles", GENERIC.content_angles),
        photo_guide=d.get("photo_guide", GENERIC.photo_guide),
        cta=d.get("cta", GENERIC.cta), persona=d.get("persona", GENERIC.persona),
        cautions=d.get("cautions", []),
        pain_points=d.get("pain_points", "") or GENERIC.pain_points,
        trust_signals=d.get("trust_signals", "") or GENERIC.trust_signals,
        example_copy=d.get("example_copy", "") or GENERIC.example_copy,
        viral_hooks=d.get("viral_hooks", ""))


def _preset_match(industry: str) -> IndustryProfile | None:
    s = (industry or "").strip().lower()
    if not s:
        return None
    for p in PROFILES.values():
        if s == p.key or s == p.name.lower():
            return p
        if any(a.lower() in s or s in a.lower() for a in p.aliases):
            return p
    return None


def resolve_industry(industry: str) -> IndustryProfile:
    """업종 → 프로필. 프리셋 매칭 → DB(AI생성/수정) 캐시 → GENERIC. (LLM 호출 안 함, 빠름)"""
    p = _preset_match(industry)
    if p:
        return p
    if industry:
        from app import db
        d = db.get_industry_profile(_slug(industry))
        if d:
            return _profile_from_dict(d)
    return GENERIC


def _to_list(s: str) -> list[str]:
    import re
    items = re.split(r"[\n,·]|^-\s*", s or "", flags=re.M)
    return [x.strip(" -#") for x in items if x.strip(" -#")][:10]


def ensure_profile(industry: str) -> IndustryProfile:
    """가게 등록 시 호출 — 프리셋/캐시에 없으면 AI로 업종 프로필 생성·저장. 실패 시 GENERIC."""
    p = _preset_match(industry)
    if p:
        return p
    if not industry:
        return GENERIC
    from app import db
    key = _slug(industry)
    cached = db.get_industry_profile(key)
    if cached:
        return _profile_from_dict(cached)
    data = _generate_ai(industry, key)
    if data:
        db.save_industry_profile(key, industry.strip(), data, source="ai")
        return _profile_from_dict(data)
    return GENERIC


def _generate_ai(industry: str, key: str) -> dict | None:
    """Claude로 업종 맞춤 프로필 생성. 키 없거나 실패 시 None."""
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        from app.generators.text_claude import _call_llm, _parse_sections
        prompt = (
            f"한국 소상공인 '{industry}' 업종의 SNS 마케팅 콘텐츠 프로필을 만들어라.\n"
            "아래 형식 그대로(대괄호 머리표 유지) 한국어로:\n"
            "[페르소나]\n(말투/톤 한 문장)\n[톤]\n(한 문장)\n"
            "[해시태그]\n(#로 시작, 쉼표로 5~7개)\n"
            "[콘텐츠앵글]\n(- 로 4개, 무엇을 찍어 올리면 좋은지)\n"
            "[촬영가이드]\n(- 로 3개)\n[CTA]\n(행동유도 한 구)\n"
            "[주의]\n(- 로 1~2개, 법적/표현 주의)\n"
            "[고객고민]\n(이 업종 손님이 겪는 진짜 고민·불안 한 문장)\n"
            "[신뢰요소]\n(구매/방문을 결정짓는 신뢰 신호들, 쉼표로)\n"
            "[예시문구]\n(이 업종에 딱 맞는 SNS 캡션 예시 2~3문장 — 공감 훅으로 시작해 구체적으로)"
        )
        raw = _call_llm(prompt, max_tokens=1200)
        d = _parse_sections(raw, ["페르소나", "톤", "해시태그", "콘텐츠앵글", "촬영가이드", "CTA", "주의",
                                  "고객고민", "신뢰요소", "예시문구"])
        if not d.get("페르소나") and not d.get("톤"):
            return None
        tags = [("#" + t.lstrip("#")) for t in _to_list(d.get("해시태그", "")) if t]
        return {
            "key": key, "name": industry.strip(), "aliases": [industry.strip()],
            "persona": d.get("페르소나", GENERIC.persona).strip(),
            "tone": d.get("톤", GENERIC.tone).strip(),
            "hashtag_seeds": tags or GENERIC.hashtag_seeds,
            "content_angles": _to_list(d.get("콘텐츠앵글", "")) or GENERIC.content_angles,
            "photo_guide": _to_list(d.get("촬영가이드", "")) or GENERIC.photo_guide,
            "cta": (d.get("CTA", GENERIC.cta).strip() or GENERIC.cta),
            "cautions": _to_list(d.get("주의", "")),
            "pain_points": (d.get("고객고민", "") or "").strip(),
            "trust_signals": (d.get("신뢰요소", "") or "").strip(),
            "example_copy": (d.get("예시문구", "") or "").strip(),
        }
    except Exception:
        return None
