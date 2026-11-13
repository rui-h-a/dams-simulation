#!/usr/bin/env python3
"""Verify the published arithmetic from fixed DAMS simulation summaries."""

import argparse
from collections import defaultdict
from csv import DictReader
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import stat
from statistics import fmean, stdev
import subprocess
import sys
import tempfile
from urllib.request import HTTPRedirectHandler, Request, build_opener


RAW_BASE = "https://raw.githubusercontent.com/rui-h-a/dams-simulation"
# Directory, public commit, then (filename, bytes, SHA-256) for each input.
PACKAGES = (
    (
        "r11-10000-paired-results", "2c921307a0aad405b515426c43c8ab156762fcd0", (
            ("SHA256SUMS", 234,
             "f86119af3b19194193b6702cb96fc7ff59548859b64b0c8b5cd4cee033ed8dfb"),
            ("figure.json", 11021,
             "60a4971aaaec50eac87582438c3e34617447114a4c6b080afba269db9bb5df01"),
            ("pooled.json", 98146,
             "9507497f0e295c93fcbf31ed6d29a9a02cb2532dbd10ab63d07973420de1eecc"),
            ("roster.json", 239,
             "ee1c8bc5378089291b9ffde35a97c860ad5ddc500714355bd5e8ee037ed377c9"),
            ("verify.py", 4109,
             "24bfe241654ba295ea04d18786d649e2d61eee33de1c731d6667faf8d1f40e25"),
        ),
    ),
    (
        "r11-10000-precision", "bffe007794a022a2efb9dc9ce9d2c5c69d2e7c6a", (
            ("summary.json", 19776,
             "9a282aaa4e677c4850a4e498e27101caff1b75190f0b75c483e73b57a4fd862c"),
            ("verify_precision.py", 11843,
             "39fa6948ba3c91ab100f90de83303eeabd27f1b93619444d7f846d69609715f8"),
        ),
    ),
    (
        "r11-10000-resources", "968ecadacedd88ad815a5f19de51de2011e853d0", (
            ("arm-resources.csv", 3930,
             "ecfe2af861458f9421043744a522c567c73e7e0f1955c18f943071013fc315b3"),
        ),
    ),
    (
        "r11-10000-scheduling", "aa7e4f77c685064825a307f9eb3b4860da4e0936", (
            ("prehistory.csv", 416,
             "72acfacf045d81cd90de467e7b39864b44c697b5976e8d927b7438a507c6df20"),
            ("results.json", 2265,
             "a9d139e0b1f9ed5eed2ac0ab3545c4a4cb4bfac18b3944b471e2aea3e0c009bb"),
            ("verify.py", 5539,
             "47d23bc2cd77f014aa144aff2c601df1d44ba7a2b3854bc1cf0a2ef7bff90dde"),
        ),
    ),
    (
        "record-procedure", "3ce886a20d9a1f088814909922adffae08d6c5b8", (
            ("manifest.json", 3307,
             "eced3c0379e7c4614d78055b91b633d1938af0dc849a838c2a2a5e1e2e903ba1"),
            ("retained-summary-rows.csv", 21035,
             "b112d22601aed200da8287f533a2e24d8c76f8e094fa6aea9b30d72d2abde3db"),
            ("verify.py", 4359,
             "f1eea57ec8523dd577e73a123c323aaaae8f7bf93e7548a1a337847455ce58b7"),
        ),
    ),
    (
        "startup-marginal", "3ce886a20d9a1f088814909922adffae08d6c5b8", (
            ("provenance.json", 1275,
             "27b041747d86dbaf19d3e12a8da41edb2a752b72aa0cdf9ad4c0ae4daac717fd"),
            ("results.json", 4514,
             "6b214c54dd2dcdfb5964e4dcb65c311649d5b0496daaa7f6aa214376fdb1ed05"),
            ("verify.py", 6579,
             "60c51bd2b2babfa13d9ecd59523775c8b49f4e7e8d7af32dbafc13f48ce2f57f"),
            ("world-endpoints.csv", 11728,
             "f8a2725ca6a294f8931db90c1e1ab8f7af4346d2f00ff28da164d01a9a8dca44"),
        ),
    ),
    (
        "acquisition-matched-worlds", "ce8c43763b823c6636c56244ab4527563354a2d2", (
            ("ma-paired-aggregates.json", 135129,
             "4aab653eeb21e454cbcf6f665e7d96137c4c46971c11c02d6ad3dcaf58829023"),
            ("ma-paired-estimates.json", 13526,
             "5378efb8fd4dc0303e82b7c01e5cd241a725f45ffbb2305a29cee0f9fd6722c7"),
            ("verify_ma.py", 5586,
             "8a11a93314c7ea941b20842619c3f214b36a5920d6de762cf530ecb834cfde65"),
        ),
    ),
    (
        "startup-regret-normalization", "ce38268c9d432df3798ff9a532df00fab6eb0eb9", (
            ("startup-regret-aggregates.json", 211902,
             "ac6d21700b3219c56ae01128a856dfe7e98a420ec171ac3a5435327f489e4c6f"),
            ("startup-regret-estimates.json", 31798,
             "8143d6ada080fbb7c5604d34fa3d7b2c491846e6e2c9eccee8e656ef6e0357a8"),
            ("verify_startup_regret.py", 4657,
             "36309acb3a0616616ec7f7e44a1faaea0492b3b334d7726123cb9dded23867d6"),
        ),
    ),
    (
        "r11-50000-checkpoints", "416d6de4325fe5dea722634ed3106c08b83f86e9", (
            ("checkpoint-files.csv", 2494,
             "c1cf4966137264e708e490e8c9d1891aa34f5eadad5244387f160e3b5af3af27"),
            ("inventory-sizes.csv", 48257,
             "80956daaa973f02ca4012d83cddcd5127d6a786094f841740e145f19f18d1f55"),
            ("provenance.json", 1799,
             "338a628fcfcb16d27b853a833452c2234c6097a71ca9ed27b30ea2773fb41f07"),
            ("results.json", 1634,
             "8072c7ea6038d72c1436a24c1b49b706dad30784cec88d24b0c4688dbd59d1b1"),
            ("verify.py", 5735,
             "d683218cacd7e94e8469b4c8bfd35247573fff9cdac7bfe91153075f69c351f1"),
        ),
    ),
    (
        "r11-10000-terminal-stock", "416d6de4325fe5dea722634ed3106c08b83f86e9", (
            ("results.json", 5437,
             "2fab39af6136574c470cd09f73abedf07e71ac8ed188297a24be5584230f544a"),
            ("terminal.csv", 15551,
             "eaff24a4232c6c03558a275900dcfc7d7a3e1083451c228c304863d15812ed46"),
            ("verify.py", 4709,
             "269d942df8486157434ac90b30dd73a822954d5fc8e73216ecd9f36ed37840ec"),
        ),
    ),
    (
        "demand-regime-tradeoffs", "830446ad3ea1929c6d1e863948a37dcdbc328f7d", (
            ("arm-outcomes.csv", 83103,
             "5614b24b8321c6c55be39ee8941004a9f5d56124b20758670e6561d9bd19b5c4"),
            ("results.json", 1709,
             "743c23c53926f2e093b15f82f27e9ae501f349d79344e5b811fd8aa1631567f1"),
            ("verify.py", 3646,
             "f6546080a6a582512d5f2ad9fc54d78d521ca536a02e372f89ec4003da691a4f"),
        ),
    ),
    (
        "first-year-voluntary-exits", "830446ad3ea1929c6d1e863948a37dcdbc328f7d", (
            ("arm-counts.csv", 48183,
             "87e4886ccc3052d0fcd6a00c4e839e86493d2092d0b501150a8c5536e2b08b19"),
            ("results.json", 2709,
             "91af9f8310eb3e05c5d78d4c7de1807dd184648c3e222d94735e72dd49161a42"),
            ("verify.py", 4127,
             "c523ebc995595a604ecdd3733b696e040f3b15e44fcf26db384a12d7a4cb80a2"),
        ),
    ),
    (
        "founding-closure-observation", "830446ad3ea1929c6d1e863948a37dcdbc328f7d", (
            ("cbs-cells.json", 2033,
             "868d25e62761362e3a924996bbdddd1c2effb7b3f48ebcb59fdb2d251ddb304e"),
            ("cbs-periods.json", 495,
             "7c49ec6c3d88d373a365d9e59d5ff050f35fdcb085cbe44f8cac9b57a5720241"),
            ("cbs-sectors.json", 553,
             "bda44db8e8a3b58b121679784d6a146b9572bf161d6326e09e10731b99816db7"),
            ("cbs-size.json", 406,
             "9a01a95d356c80f5858ec2a8496aa0e2e4c9dab00287121dd251471c6a98ce8c"),
            ("cbs-source.json", 1875,
             "658cbd5c52903ae4c2f957fd0ac98b56df599e22a070aa3f1ea2f2fa9b30de6b"),
            ("cbs-table.json", 6215,
             "5b001ec7dc9add31f98e1118ccc1195322fde74f4663c87f0d4f006106e28072"),
            ("cbs-topics.json", 1093,
             "44c8c17c576431c4633ac22dc732ab0df2a9f2d004f667a94339d746429d6b93"),
            ("results.json", 13902,
             "ce6026b30a22c128f811d3502112ddef71b6afffe8e4c4adb5d19c739249ce62"),
            ("verify.py", 7832,
             "64075542d19248b00d9f16f30ef5c39f55581b32c0854d8c63656a0c3979cf11"),
        ),
    ),
    (
        "r11-10000-operating-payback", "830446ad3ea1929c6d1e863948a37dcdbc328f7d", (
            ("annual-inputs.csv", 5914,
             "0fb221775fe439e73e47a2186a6fb2598388fabbac37470124c7022f73af8335"),
            ("provenance.json", 11642,
             "c7290d1bdee41518f3590836237628dbb64e5fc60ea46ec748b9d41f6d9ff188"),
            ("results.json", 6948,
             "d0fa84b71f7192020019ee7f79016c1611eff0f613a1cfd906051e75296180ec"),
            ("verify.py", 7071,
             "1b6b88bcf7c75b81bbe8719638da0d4974986d6bdfa265df49201f6d7c85ec69"),
        ),
    ),
    (
        "r11-10000-outstanding-record-days", "830446ad3ea1929c6d1e863948a37dcdbc328f7d", (
            ("arm-totals.csv", 4557,
             "4141e402db7aab8b982d27d6bbf8108aab65fe9fba9dd3c5202ced09d4beec39"),
            ("results.json", 2775,
             "4a92926db7ccf237be030aaf7a90b962c852bec47064c1ee465d546b2d72fab8"),
            ("verify.py", 4453,
             "b92886b01ba87514681581cce7e59b6137cb2876ff3ff38cb4c340a687fe63f6"),
        ),
    ),
    (
        "r11-10000-payback-sensitivity", "830446ad3ea1929c6d1e863948a37dcdbc328f7d", (
            ("results.json", 8145,
             "c471e39580bfb61422e3a474322e188edf76da8623fa2e2f6ba01daa31ffbcee"),
            ("verify.py", 3411,
             "53cd36f6b332b824c6c950234dfeabcade981e587bac11eccc2094a01b9314f3"),
        ),
    ),
    (
        "r11-10000-recognition-flows", "830446ad3ea1929c6d1e863948a37dcdbc328f7d", (
            ("arm-flows.csv", 10501,
             "8dd4cba6a758c3d37ecc78a2931f7f32cb94614d17cd3c9a22a5d9f5c71faebb"),
            ("results.json", 9038,
             "f3aef15f49bb7ef024c0acc03a1a46e4a1ffcaf2e6efb6be5740e68c6c317a49"),
            ("verify.py", 3067,
             "7802b48f18a712f06bebcd7b3abe1cdbd01cb3af05622d9b040bf0605d554acf"),
        ),
    ),
    (
        "r11-10000-recognition-stages", "830446ad3ea1929c6d1e863948a37dcdbc328f7d", (
            ("arm-stages.csv", 17129,
             "5289bf10431435d330ce083b6fdd17f3ef80afe6db9621a375866e52f664bb84"),
            ("results.json", 2979,
             "7165690e4e4fa3608febf704bfa2ebec8c95cef89dbf6b0533964e8f7be2e4e3"),
            ("verify.py", 3597,
             "a5ceb4c49001983e2673ade3d21f526a62d68ff888912aae1196a285d6db02de"),
        ),
    ),
    (
        "record-intake-exclusion", "830446ad3ea1929c6d1e863948a37dcdbc328f7d", (
            ("provenance.json", 12252,
             "215a88f10ce2f66aaed4babdc9635e04302996344bd4c6f1efdadcd40ca10d11"),
            ("results.json", 5166,
             "5ff4eaeca364fffc330314af99dd4183d9696419660636b9f2a47127eadd5e75"),
            ("verify.py", 6308,
             "b121ebdc70608b8c0985660586d8041ddcd1657a8f99fd47f3c673ee2a3b2895"),
            ("world-outcomes.csv", 4641,
             "7832b59f5b99a0866edb4d96cb3af8b3def70c19225ca1a743ef40c9d0a932ae"),
        ),
    ),
    (
        "traditional-demand-matched", "830446ad3ea1929c6d1e863948a37dcdbc328f7d", (
            ("arm-outcomes.json", 385035,
             "e452b2c313fbdae91ccb4e725b5df0d48002c353b0f3e9a8b7bb2d58587d8457"),
            ("results.json", 13084,
             "5f8dc2dce6c1916640929142f19506de25c2094f6b4db5b2861c996371e9ef04"),
            ("verify.py", 5933,
             "af9bd73a3876e22189d665cb855d75f4a46fd82400bd8ddeed9f2353843f9fec"),
        ),
    ),
    (
        "voluntary-quit-observation", "830446ad3ea1929c6d1e863948a37dcdbc328f7d", (
            ("bls-monthly.csv", 6026,
             "72a38c2706e7717d26b138d8313e42c3bea22fe12b388bf46af2f59f8b0a6fbb"),
            ("bls-provenance.json", 997,
             "b9bb9c4f0ea4f6785a1d08c8fb54a01f988b78e0647ac0f273aeef24aa5a8364"),
            ("founding-quits-provenance.json", 108857,
             "c8ae76006299367036b9b82667de392be103557db5a8d0b73874e35917fa8e39"),
            ("founding-quits.csv", 616912,
             "9780222e564a84e24b4779e6d61bf58df09deba702890ade98d447db2698ba05"),
            ("main-quits-provenance.json", 318458,
             "66269df138a2179e127b31e5d261df981c35cb0dd1032daa78c15f74728e0f84"),
            ("results.json", 4396,
             "70318aee6eddb5f4c0bd9213e95fb2023d8a295999f0730c3657a90d47005d7f"),
            ("verify.py", 4855,
             "4e678eb0283976e648e67008447602dcc34a3d8b62651197252e705f75168d41"),
        ),
    ),
    (
        "r11-10000-post-adoption", "fc83b14aa3f1cf29fd9c71017825c1ea6ab87503", (
            ("arm-totals.csv", 17290,
             "dec0ebf6863d6acb3c0d358b674c25a437c3f63b144a127d141f1a3e0dd0c5b4"),
            ("manifest.json", 1843,
             "dfe985120c587e8c66445094cfc2a2efb7792f3a8968b6a52fc817f06d2c29b7"),
            ("verify.py", 5249,
             "dd2ea0c2f3d78d64dfc999b6e076fa3b72578529cb1f86d20504ae1f2b22657b"),
        ),
    ),
    (
        "thesis-extended", "e229ec5095ca25d9d803d55267753d131299581d", (
            ("manifest.json", 2465,
             "c6e89579ce6115ee7de08220400d14d80499717e5e25a177437ffaad24045a20"),
            ("README.md", 1417,
             "3beae504370ed5d06a99bac3fe05a75cb393cb13e551c386f70ad5809504378f"),
            ("backlog-history/README.md", 1211,
             "a307d441c15c8e6203742a86e65749585aef6bd2a7e845b7fbed691926976dc0"),
            ("backlog-history/SHA256SUMS", 389,
             "7c36af16914b9aac6107a2d802139ce1de83fb0b2f29114801382166ee662648"),
            ("backlog-history/daily.csv.gz", 938549,
             "67469933d0d329a05d909ec6649f5d033fa4cb9f7c6dcd12dd1492c164790257"),
            ("backlog-history/results.json", 681,
             "894b63ef3b600f784330efbbbe0eea5faf1c12f099a4ff9bd6cd388b401451c2"),
            ("backlog-history/terminal.csv", 15551,
             "eaff24a4232c6c03558a275900dcfc7d7a3e1083451c228c304863d15812ed46"),
            ("backlog-history/verify.py", 5648,
             "2db354855182a263e93634f9578babe1f0e735ea02fe8d2cfcd7bfe6eab917c0"),
            ("record-procedure-three-arm/README.md", 2493,
             "6ce8fbc83e8249105873a03901b13a0372f5db0fb973d56b01c3e5c13ce7eea0"),
            ("record-procedure-three-arm/manifest.json", 4670,
             "2814c3a0f9969af586d2eb4ff52257192981764f787625833715888de8a52917"),
            ("record-procedure-three-arm/retained-summary-rows.csv", 69513,
             "d79c1612615f876eeaae799685955cfa506cedd12f2cea39eab51e27f4369c9b"),
            ("record-procedure-three-arm/verify.py", 6384,
             "e7bfbd152cb789f121b9d1308233a61bcd3973d181d84803bb63fad6ab24f05f"),
            ("startup-marginal-extension/README.md", 2317,
             "fb61daf56ea99884f5bdc45d055a1f7233f000d3d4963d2531760a0eaaba4291"),
            ("startup-marginal-extension/expected-results.json", 10801,
             "e75ce9b5075c33b638ecf56b952dc61ed7d10068ceea072ae4f3abf28d547f1e"),
            ("startup-marginal-extension/provenance.json", 1275,
             "27b041747d86dbaf19d3e12a8da41edb2a752b72aa0cdf9ad4c0ae4daac717fd"),
            ("startup-marginal-extension/verify.py", 6579,
             "60c51bd2b2babfa13d9ecd59523775c8b49f4e7e8d7af32dbafc13f48ce2f57f"),
            ("startup-marginal-extension/verify_extensions.py", 5281,
             "2b7d7e3c0f66a20a5641498f086643c31db1c95988984cbce35c3419b4eae1cd"),
            ("startup-marginal-extension/world-endpoints.csv", 11728,
             "f8a2725ca6a294f8931db90c1e1ab8f7af4346d2f00ff28da164d01a9a8dca44"),
            ("verify.py", 1324,
             "5f5cb31ccd4ea6c993796a0a3d1668733db4db58a30a0d88d3c1306747639e04"),
        ),
    ),
    (
        "acquisition-period-results", "a4610ab31b3e77d7910939f483a035e0f2b1d112", (
            ("arm-periods.csv", 158214,
             "a69e1c66a09b86561337a4c9be3f78f85d783600adccee895ebf53dd5bc401df"),
            ("verify.py", 3549,
             "29c8f184878c9b3ee0967ce72c870d3775265d4db4834d09115155d5f97a9907"),
        ),
    ),
    (
        "r11-10000-world-influence", "819cc1570837b07adb2cce6eafb2932276bc23f3", (
            ("verify.py", 3427,
             "ea36eb27ca801f071ac96a3775fd4d5d353e348fb06105f295426ddf9f48c359"),
        ),
    ),
    (
        "short-model-confirmation-volume", "f94793f0e5c746ee6fb68c1ca0bd745f73bf5f14", (
            ("confirmation_counts.csv", 5203,
             "be361f09786683b460f27d3e9ce23a00cafc16250fa362dfebcd530a18a0ba49"),
            ("verify.py", 2643,
             "6720f0c823844690ccc0fbb4edf7b5b90e56268ce4f2f1fab21d24b15658e396"),
            ("world-delay-counts.csv", 17504,
             "c20a5d600e66a0df20fe32184cc02eead3831e740733554cf3285b375c85828a"),
        ),
    ),
    (
        "short-model-lower-half-authority", "a162193f13e428960262186a786091b9e41c1a13", (
            ("verify.py", 1909,
             "622c0a7330d25867401b3d64cc77e63210e71dcdab32d20bdf44c9de1f9cff36"),
            ("world-half-shares.csv", 2863,
             "2b1da98302c427bbf54470b02349210341bea87eeb01af432d93ad5bf32d44f6"),
        ),
    ),
    (
        "short-model-voting-weight", "7dfb32573b3988c4bfbd57cc9dfe8a8705d5812d", (
            ("daily-voting.csv", 496624,
             "2998ba6f1f6ffc1e85154f46dbe740b30c8f0dc409daee3efee3772482f49eab"),
            ("verify.py", 2915,
             "a23ae4f083a2418f569bdfa87fc9d7251d0a377fd714f7287f1f461692dd3deb"),
        ),
    ),
    (
        "short-model-work-contrasts", "2e6039122348dfebf813741bc5de6a2583714d61", (
            ("verify.py", 1981,
             "1edffb3ac4380086e1f59ec94a956f0e619d57a216fef25d077edf839fcf40b5"),
            ("world-work.csv", 5396,
             "f90836ed0ac63f5b0e95152fa2b91beb571ae4d9c1fc2671095f3722e4d39921"),
        ),
    ),
    (
        "short-model-quorum-outage-stocks", "d3837e738ff26cb2039aa81c38652e5b315cd383", (
            ("outage-delays.csv", 946,
             "c25d844e98fa1c5f897f8061c4236fd9233b9525af54aa901b91ad3377eb1743"),
            ("outage-stocks.csv", 2903,
             "d746cf769a6e0bd0c005123890e7ee582bd7fd9c1d792e6d4e898498d2aac76a"),
            ("verify.py", 2835,
             "d741c830a4646615c349ecc3dd00982bc97bc3c184792646bbf033422b392eea"),
        ),
    ),
    (
        "r11-100000-scale-check", "bc27d8a6a5db83c9d48f734a9ce4275d55b64c98", (
            ("evidence.json", 1663,
             "d4ff7f8129487e2e71c20ec571ff002c7fe9ac67d93842bb30a041384625fd36"),
            ("verify.py", 1754,
             "d208ccdaeb91cf00326375ce5c8d3bf69b55a60be315057a9dd1dbe5b0a1dd0b"),
        ),
    ),
    (
        "short-model-workplace-disruption", "a6a0b04b9bc0c38cf2d3d993645ba33db66bd7c8", (
            ("paired-work.csv", 129297,
             "b751ba74a20b0eb610d292941e05fce776ef9ca376cdd549340cbb19f64dcd9f"),
            ("provenance.json", 48294,
             "9e7aba95763c0dd05e234b7f1e2b40c6ebe28bfdc2bffbcebafa6e1c31c685f0"),
            ("verify.py", 2937,
             "fc731b167b61e7fb298f5d059286d7bf64a5f55338b197978dd51c5d52dbfcda"),
        ),
    ),
    (
        "r11-50000-formal-disposition", "900a940ac73f6a3e2d8e60f7d02b72d286eb867f", (
            ("evidence.json", 10105,
             "bee004245bcfa497b417321d85180ce83e4c27aa553fb79e93c80a8c3425eadb"),
            ("verify.py", 1781,
             "92fe2e7047d50f1aa9320152e1a9b8958c7be70a553b4bab1396fde90f38a5c1"),
        ),
    ),
    (
        "r11-decision-execution", "5a8b61f7af9fa71b76604b76c97d22e2b39d5ce8", (
            ("verify.py", 1807,
             "5caaa4cbf92ce198c7b7645fb0d16911653cf642f2f31faf67e2b0f4771d6590"),
            ("world-pairs.json", 25767,
             "f897b26ce301e49ebae31280c94bd280230139886724bf612905f68ea8b4695a"),
        ),
    ),
    (
        "traditional-adoption-timing", "6d267d1ee99aaa7d60147d11a1300c987d4c505d", (
            ("paired-worlds.json", 38181,
             "80f3482de576bf8ba888544cef8ea6a27c50dd46b2ea15dbda66e424c4adc7cc"),
            ("verify.py", 1236,
             "a3b5936e0674d3d76e6a0a484724b5baa5c0fc232d06aa4850b360435734e766"),
        ),
    ),
    (
        "traditional-adoption-confirmation-stocks", "c81444747d627fdee2a279eda9350303fb7c02cf", (
            ("terminal-counts.json", 39265,
             "3dfc7e33ae085eedab3a16e740924f8bcca1cdde3e77275a5bce8f7284b79afe"),
            ("verify.py", 1372,
             "349316aa5881ca5bf3dd515f8715ff8d2edd5cf30a753b0cded6ead261ab4358"),
        ),
    ),
    (
        "traditional-adoption-phases", "5d5c06de5cef9b4795b108cd798f909448a018dd", (
            ("phase-totals.json", 66394,
             "0f5a54faabeb170e53624c2099f6671f36ba945b3b695dc268429eda48e169ce"),
            ("verify.py", 1557,
             "81c8cc742398c796224852abfe0b909579cbd0092ea9caaacbb9755d936a8a9e"),
        ),
    ),
    (
        "r11-10000-backlog-by-year", "24de59d3bdbba9b4cc8cfca41afbe47564ba1777", (
            ("annual-stocks.json", 22458,
             "25c81d201e2e6b4d30231f5179aa0f8895852f3d1a53b3daa6219993bc8409bd"),
            ("verify.py", 5254,
             "7abef92bd13d0abd23a3875d8744c06d26bfab58db692e19c3b64fe8ace29e69"),
        ),
    ),
    (
        "record-procedure-costs", "e8fdc6f39af9e1d33ecebfda3fd2a1f69d9d4c00", (
            ("arm-costs.json", 84756,
             "80311a182a4a612731dbaa329e2dc480536f1d43ee85f591bed4c9f9abfaeff7"),
            ("verify.py", 2398,
             "76a2a6dff005b1e36790000ce4e294c0a75c222045b9cedc005af816f884558c"),
        ),
    ),
    (
        "traditional-ten-year-windows", "f3b4b82d9faba7ed085a3414c7777f0778b3cbde", (
            ("annual-arm-aggregates.csv", 89784,
             "8a613fe93ecc10492ce955e51fbd0fad40fe2908728fe252a832eccd0cd3c00f"),
            ("verify.py", 2453,
             "5cf41df0675ddb19f2569758613d3320015d5830c9d05033ca86fa6b8afa5c42"),
        ),
    ),
)

CORE = "r11-10000-paired-results"
QUIT_OBSERVATION = "voluntary-quit-observation"
PRECISION = "r11-10000-precision"
RESOURCE = "r11-10000-resources"
SCHEDULE = "r11-10000-scheduling"
RECORD = "record-procedure"
MARGINAL = "startup-marginal"
ACQUISITION = "acquisition-matched-worlds"
REGRET = "startup-regret-normalization"
CHECKPOINT = "r11-50000-checkpoints"
STOCK = "r11-10000-terminal-stock"
STANDARD_REPORTS = (
    "demand-regime-tradeoffs",
    "first-year-voluntary-exits",
    "r11-10000-operating-payback",
    "r11-10000-payback-sensitivity",
    "r11-10000-recognition-flows",
    "r11-10000-recognition-stages",
    "record-intake-exclusion",
    "traditional-demand-matched",
    QUIT_OBSERVATION,
)
CLOSURE = "founding-closure-observation"
RECORD_DAYS = "r11-10000-outstanding-record-days"
BACKLOG_YEARS = "r11-10000-backlog-by-year"
BACKLOG_YEARS_SHA256 = "f2fa93c0c33eb4bbf04f59ff9fcad1aefef4183e175a5880bac0d180ce60de5b"
POST_ADOPTION = "r11-10000-post-adoption"
OPERATING_PAYBACK = "r11-10000-operating-payback"
EXTENDED = "thesis-extended"
RECORD_COSTS = "record-procedure-costs"
POST_ADOPTION_REPORT_SHA256 = "699e4fc954139a7dd50b5d0a51fb436d1b15196aca53933e83de02b5e38d5e1a"
PINNED_REPORTS = {
    "acquisition-period-results": ("61cccf12c40af29c05cd78d11cc968214d3722db5816a1b63e55618bcc3b5372", ()),
    "r11-10000-world-influence": ("2ff6356d05a34158702b8712cc99aca62b3546e480e3d408fc5ec398d4a54dc7", ("r11-10000-paired-results/pooled.json",)),
    "short-model-confirmation-volume": ("3d8b08c77c55a96502347dc41e9e65511a02247f1083bc70afa30483c52f3c7c", ()),
    "short-model-lower-half-authority": ("2daad55a9b41044d482e730930a5e3ff48dc436bdccd171dea1b56d6675cca09", ()),
    "short-model-voting-weight": ("20ca4295cf1071d80852d89e0c9241d8e696476d796bb24dc3758131d17d90a3", ()),
    "short-model-work-contrasts": ("b546a63cd1ed2c0e2adca3267b4256c0879b0bbde7198f43d2f3bb11090a8c77", ()),
    "short-model-quorum-outage-stocks": ("55dd756e290afd721baa0739b5c68fc8dec0812fd63e1a4c8a6c5f5a0946e26f", ()),
    "r11-100000-scale-check": ("8bc6fc384b6bea503925810612161972a397534214453bc65f759b944c9575a7", ()),
    "short-model-workplace-disruption": ("0786923bc52da008f3b3c93e55fb9d098a110f35fce4608a5a1b58bd83cae3d4", ()),
    "r11-50000-formal-disposition": ("898346c5207c6872ca44050d40ccf417aaecaf952187b641bbc6e2d0a2250c40", ()),
    "r11-decision-execution": ("224bf8e70473bea8a2d74c4d9872c7a66efbf1abd76ec2e2a934ce6e8ef0296a", ()),
    "traditional-adoption-timing": ("0852e0c92581fe4b5faa2f1efda535c9ff96e88ad8b97815de46ee8f52c4094b", ()),
    "traditional-adoption-confirmation-stocks": ("9249dd3feed7a782fc67e664b88346e4c94bcc2c9ed6ec4c1fa8794a2cce7ae0", ()),
    "traditional-adoption-phases": ("0b1f3adbdb3e00a17fd2bf9f2075c4f3f4201748e67873fa3d8bd69e7319a1b9", ()),
    "record-procedure-costs": ("6ef8811195216210d2eb2f51602b57c3ea7996b439a1c42a8d4ee50cc0642bd6", ()),
    "traditional-ten-year-windows": ("9e280c544c06a8a458245d06321a361770773a5d7b9e4e54bb986f9b39be54a2", ()),
}

CORE_REPORT = [
    "Verified 75 paired outcome summaries across 10 worlds.",
    "Verified plotted differences and adjusted intervals for 12 primary comparisons.",
]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_cached(path, size):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"Cache entry is not a regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise ValueError(f"Cache entry is not a regular file: {path}")
        return file.read(size + 1)


def require_pin(data, size, digest, label):
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"Byte count or SHA-256 mismatch: {label}")


def obtain(cache, opener, package, commit, name, size, digest):
    cached = cache / digest
    label = f"{package}/{name}"
    try:
        data = read_cached(cached, size)
    except FileNotFoundError:
        if opener is None:
            raise ValueError(f"Offline cache is missing: {label}") from None
        url = f"{RAW_BASE}/{commit}/evidence/{label}"
        request = Request(url, headers={"User-Agent": "DAMS thesis evidence verifier"})
        with opener.open(request, timeout=20) as response:
            data = response.read(size + 1)
        require_pin(data, size, digest, label)
        with tempfile.NamedTemporaryFile(dir=cache, prefix="download-", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            try:
                temporary.write(data)
                temporary.close()
                os.replace(temporary_path, cached)
            finally:
                temporary_path.unlink(missing_ok=True)
        return data
    require_pin(data, size, digest, f"{label} (cached as {cached})")
    return data


def compare(actual, expected, label):
    """Allow final-digit floating-point variation while checking the whole report."""
    if type(actual) is not type(expected):
        raise ValueError(f"Unexpected result type at {label}")
    if isinstance(expected, dict):
        if actual.keys() != expected.keys():
            raise ValueError(f"Unexpected result fields at {label}")
        for key in expected:
            compare(actual[key], expected[key], f"{label}.{key}")
    elif isinstance(expected, list):
        if len(actual) != len(expected):
            raise ValueError(f"Unexpected result length at {label}")
        for index, (value, reference) in enumerate(zip(actual, expected)):
            compare(value, reference, f"{label}[{index}]")
    else:
        same = math.isclose(actual, expected, rel_tol=1e-9) if isinstance(expected, float) else actual == expected
        if not same:
            raise ValueError(f"Unexpected result at {label}")


def run_output(workspace, package, script, *args):
    command = [sys.executable, "-I", "-S", "-B", "-X", "utf8", str(workspace / package / script), *map(str, args)]
    result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, encoding="utf-8", timeout=90)
    if result.returncode:
        error = result.stderr.strip() or result.stdout.strip()
        raise ValueError(f"{package} verifier failed (exit {result.returncode}): {error}")
    return result.stdout


def run_report(workspace, package, script, *args):
    return json.loads(run_output(workspace, package, script, *args))


def read_report(workspace, package, filename):
    return json.loads((workspace / package / filename).read_bytes())


def verify_record_cost_sources(workspace):
    """Check that both packets use the same original summaries and denominators."""
    path = workspace / EXTENDED / "record-procedure-three-arm" / "retained-summary-rows.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        original = {(int(row["world"]), row["arm"]): row for row in DictReader(stream)}
    costs = read_report(workspace, RECORD_COSTS, "arm-costs.json")
    if len(costs) != len(original):
        raise ValueError("Record procedure cost roster size differs")
    fields = ("active_member_calendar_days", "revenue_resource_units", "operating_resource_units")
    for row in costs:
        key = row["world"], row["arm"]
        source = original.pop(key, None)
        if (source is None or row["summary_sha256"] != source["source_summary_sha256"]
                or any(row[field] != float(source[field]) for field in fields)):
            raise ValueError(f"Record procedure cost source differs for {key}")


def verify_secondary_outcomes(workspace):
    """Recompute the three unadjusted DAMS-minus-hierarchy intervals."""
    paired = read_report(workspace, CORE, "pooled.json")["comparisons"]["dams-witness"]
    outcomes = {
        "delivered_share_of_orders": (-0.006862224781, -0.006981686923, -0.006742762640),
        "learning_hours_per_member_year": (-0.142564632590, -0.156711576205, -0.128417688974),
        "material_logistics_per_delivered_unit": (0.000003278478, -0.000010092923, 0.000016649878),
    }
    for outcome, expected in outcomes.items():
        rows = paired[outcome]["world_values"]
        if sorted(world for world, _, _ in rows) != list(range(1000, 1010)):
            raise ValueError(f"Secondary outcome roster differs: {outcome}")
        differences = [dams - hierarchy for _, dams, hierarchy in rows]
        center = fmean(differences)
        margin = 2.2621571628540993 * stdev(differences) / math.sqrt(len(differences))
        actual = (center, center - margin, center + margin)
        if any(not math.isclose(value, reference, rel_tol=0, abs_tol=5e-12)
               for value, reference in zip(actual, expected, strict=True)):
            raise ValueError(f"Secondary outcome differs: {outcome}")


def verify_quit_dynamics(workspace):
    """Check absolute same-month changes in annualized quit flows."""
    root = workspace / QUIT_OBSERVATION
    with (root / "bls-monthly.csv").open(encoding="utf-8", newline="") as stream:
        bls = {(r["series"], int(r["year"]), int(r["month"])): 12 * Fraction(r["quits_per_100_employed"])
               for r in DictReader(stream)}
    founding = defaultdict(list)
    with (root / "founding-quits.csv").open(encoding="utf-8", newline="") as stream:
        for row in DictReader(stream):
            if row["arm"] == "control-hierarchy":
                period = int(row["year"]), int(row["month"])
                founding[period].append(Fraction(1200 * int(row["voluntary_quits"]), int(row["active_on_twelfth"])))
    main = defaultdict(list)
    for world in read_report(workspace, QUIT_OBSERVATION, "main-quits-provenance.json")["worlds"].values():
        for year, row in world["arms"]["control-hierarchy"]["annual"].items():
            counts = zip(row["voluntary_exits_by_month"], row["active_members_on_monthly_12th"], strict=True)
            for month, (quits, headcount) in enumerate(counts, start=1):
                main[int(year), month].append(Fraction(1200 * quits, headcount))

    comparisons = (
        (founding, 57, "JTS100000000000004QUR", 2022, (1.93, 5.33)),
        (main, 10, "JTS100000000000000QUR", 2023, (1.21, 3.67)),
    )
    for rates, worlds, series, first_year, expected in comparisons:
        changes = []
        reference = []
        for year in range(first_year, first_year + 3):
            for month in range(1, 13):
                previous, current = rates[year - 1, month], rates[year, month]
                if len(previous) != worlds or len(current) != worlds:
                    raise ValueError(f"Monthly model roster differs for {series}, {year}-{month:02}")
                changes.append(abs(sum(current) - sum(previous)) / worlds)
                reference.append(abs(bls[series, year, month] - bls[series, year - 1, month]))
        actual = tuple(round(sum(values) / len(values), 2) for values in (changes, reference))
        if actual != tuple(Fraction(str(value)) for value in expected):
            raise ValueError(f"Mean absolute quit-flow changes differ for {series}")


def verify_post_adoption_controls(report):
    """Check five-year contrasts between arms that share a transition or record."""
    comparisons = report["comparisons"]
    operating = "net_operating_result_per_member_year"
    regret = "decision_regret_per_million_available_member_hours"
    checks = (
        (operating, "control-process", "control-hierarchy", (-27.43, -28.57, -26.29)),
        (operating, "dams-witness", "control-process", (-7.77, -9.10, -6.45)),
        (operating, "linear-witness", "control-process", (7.20, 5.73, 8.68)),
        (operating, "dams-witness", "linear-witness", (-14.97, -15.65, -14.30)),
        (regret, "dams-witness", "linear-witness", (-2.81, -3.21, -2.41)),
    )
    worlds = report["worlds"]
    if worlds != list(range(1000, 1010)):
        raise ValueError("Unexpected five-year paired roster")
    for outcome, arm, reference, expected in checks:
        arm_values = comparisons[arm][outcome]["values_by_world"]
        reference_values = ([0.0] * len(worlds) if reference == "control-hierarchy"
                            else comparisons[reference][outcome]["values_by_world"])
        differences = [x - y for x, y in zip(arm_values, reference_values, strict=True)]
        if len(differences) != len(worlds):
            raise ValueError("Incomplete five-year paired contrast")
        center = fmean(differences)
        margin = 2.2621571628540993 * stdev(differences) / math.sqrt(len(differences))
        if tuple(round(v, 2) for v in (center, center - margin, center + margin)) != expected:
            raise ValueError(f"Unexpected five-year contrast: {outcome}, {arm} versus {reference}")


def verify_group_formal_shares(workspace):
    """Recompute Table 28 and the direct witnessed-policy comparisons."""
    pooled = read_report(workspace, CORE, "pooled.json")
    worlds = list(range(1000, 1010))
    if pooled["worlds"] != worlds or pooled["reference"] != "control-hierarchy":
        raise ValueError("Unexpected group-share reference or world roster")
    arms = ("control-process", "dams-witness", "linear-witness")
    # Values at manuscript precision in units of 10**-4, in arm order.
    expected = {
        "share_gap_female_minus_male": (2.59, (0.04, -0.25, 0.33), (1.74, 1.29, 2.20),
                                      (3.52, 2.95, 4.09), (-1.78, -1.98, -1.57)),
        "share_gap_migrant_minus_local": (-2.72, (-0.07, -0.55, 0.41), (-2.50, -3.21, -1.79),
                                        (-1.31, -2.71, 0.10), (-1.19, -2.01, -0.38)),
        "share_gap_caregiver_minus_other": (10.78, (-0.07, -0.45, 0.31), (7.85, 7.35, 8.36),
                                          (16.11, 15.37, 16.85), (-8.25, -8.73, -7.78)),
        "share_gap_minority_minus_majority": (-0.06, (0.09, -0.16, 0.34), (0.09, -0.44, 0.62),
                                            (0.22, -0.45, 0.89), (-0.13, -0.39, 0.14)),
    }

    def interval(differences):
        center = fmean(differences)
        margin = 2.262157162798205 * stdev(differences) / math.sqrt(len(worlds))
        return tuple(round(1e4 * x, 2) for x in (center, center - margin, center + margin))

    for outcome, published in expected.items():
        values = {}
        hierarchy = None
        for arm in arms:
            records = pooled["comparisons"][arm][outcome]["world_values"]
            if sorted(world for world, _, _ in records) != worlds:
                raise ValueError(f"Unexpected group-share world roster: {outcome}/{arm}")
            ordered = sorted(records)
            reference = tuple(value for _, _, value in ordered)
            if hierarchy is not None and hierarchy != reference:
                raise ValueError(f"Inconsistent hierarchy group shares: {outcome}/{arm}")
            hierarchy = reference
            values[arm] = tuple(value for _, value, _ in ordered)
            if not all(math.isfinite(x) for x in (*values[arm], *hierarchy)):
                raise ValueError(f"Nonfinite group shares: {outcome}/{arm}")

        def compare_arms(left, right):
            return interval([x - y for x, y in zip(left, right, strict=True)])

        actual = (round(1e4 * fmean(hierarchy), 2),
                  *(compare_arms(values[arm], hierarchy) for arm in arms),
                  compare_arms(values["dams-witness"], values["linear-witness"]))
        if actual != published:
            raise ValueError(f"Unexpected formal-share comparison: {outcome}")


def verify_main_adoption_windows(workspace):
    """Reconcile annual totals and recompute the year-window comparisons."""
    worlds = range(1000, 1010)
    arms = ("control-hierarchy", "dams-witness")
    years = range(1, 6)
    annual = {}
    path = workspace / OPERATING_PAYBACK / "annual-inputs.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        for row in DictReader(stream):
            key = (int(row["world"]), row["arm"], int(row["year_after_adoption"]))
            exposure = Fraction(row["member_years"])
            if key in annual or exposure <= 0:
                raise ValueError(f"Invalid or duplicate adoption-window record: {key}")
            annual[key] = exposure, exposure * Fraction(row["net_operating_resource_units_per_member_year"])
    if annual.keys() != {(world, arm, year) for world in worlds for arm in arms for year in years}:
        raise ValueError("Unexpected annual adoption-window roster")

    def aggregate(world, arm, window):
        exposure, net = zip(*(annual[world, arm, year] for year in window), strict=True)
        return sum(exposure), sum(net)

    five_year = {}
    path = workspace / POST_ADOPTION / "arm-totals.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        for row in DictReader(stream):
            if row["arm"] not in arms:
                continue
            key = (int(row["world"]), row["arm"])
            if key in five_year:
                raise ValueError(f"Duplicate five-year total: {key}")
            five_year[key] = (Fraction(row["active_member_calendar_days"]) / Fraction("365.25"),
                              Fraction(row["revenue_resource_units"]) - Fraction(row["operating_resource_units"]))
    if five_year.keys() != {(world, arm) for world in worlds for arm in arms}:
        raise ValueError("Unexpected five-year reconciliation roster")
    for (world, arm), (exposure, net) in five_year.items():
        annual_exposure, annual_net = aggregate(world, arm, years)
        if abs(annual_exposure - exposure) >= Fraction("1e-9") or abs(annual_net - net) >= Fraction("1e-5"):
            raise ValueError(f"Annual and five-year operating totals differ: {world}/{arm}")

    def total_difference(window):
        return sum(aggregate(world, arms[1], window)[1] - aggregate(world, arms[0], window)[1]
                   for world in worlds)

    five_year_difference = total_difference(years)
    shares = tuple(round(100 * total_difference(window) / five_year_difference, 2)
                   for window in (range(1, 2), range(1, 4)))
    late_rates = []
    for world in worlds:
        (hierarchy_exposure, hierarchy), (dams_exposure, dams) = [
            aggregate(world, arm, range(4, 6)) for arm in arms]
        late_rates.append(dams / dams_exposure - hierarchy / hierarchy_exposure)
    center = fmean(late_rates)
    margin = 2.262157162798205 * stdev(late_rates) / math.sqrt(len(worlds))
    if shares != (Fraction("70.76"), Fraction("85.23")) or tuple(
            round(value, 2) for value in (center, center - margin, center + margin)
    ) != (-13.72, -14.91, -12.53):
        raise ValueError("Unexpected operating result by adoption window")


def verify(workspace):
    precision = run_report(workspace, PRECISION, "verify_precision.py", workspace / CORE)
    compare(precision, read_report(workspace, PRECISION, "summary.json"), PRECISION)
    # The precision script hashes all five paired-result files and runs the core verifier.
    compare(precision["source"]["original_verifier_output"], CORE_REPORT, CORE)

    scheduling = run_report(workspace, SCHEDULE, "verify.py", workspace / RESOURCE / "arm-resources.csv")
    compare(scheduling, read_report(workspace, SCHEDULE, "results.json"), SCHEDULE)

    record = run_report(workspace, RECORD, "verify.py")
    manifest = read_report(workspace, RECORD, "manifest.json")
    compare(record, {
        "worlds": len(manifest["worlds"]),
        "arm_rows": manifest["arm_rows"],
        "difference": "dams-consensus minus dams-central",
        "contrasts": manifest["contrast"],
    }, RECORD)

    for package, script, expected in (
        (MARGINAL, "verify.py", "results.json"),
        (ACQUISITION, "verify_ma.py", "ma-paired-estimates.json"),
        (REGRET, "verify_startup_regret.py", "startup-regret-estimates.json"),
        (CHECKPOINT, "verify.py", "results.json"),
    ):
        compare(run_report(workspace, package, script), read_report(workspace, package, expected), package)

    stock = run_report(workspace, STOCK, "verify.py", "--print-results")
    compare(stock, read_report(workspace, STOCK, "results.json"), STOCK)

    for package in STANDARD_REPORTS:
        compare(run_report(workspace, package, "verify.py"), read_report(workspace, package, "results.json"), package)

    compare(run_report(workspace, CLOSURE, "verify.py"), read_report(workspace, CLOSURE, "results.json")["cbs"], CLOSURE)
    history = workspace / EXTENDED / "backlog-history" / "daily.csv.gz"
    compare(run_report(workspace, RECORD_DAYS, "verify.py", "--history", history),
            read_report(workspace, RECORD_DAYS, "results.json"), RECORD_DAYS)

    annual = run_output(workspace, BACKLOG_YEARS, "verify.py", "--history", history)
    if hashlib.sha256(annual.encode("utf-8")).hexdigest() != BACKLOG_YEARS_SHA256:
        raise ValueError(f"Unexpected result at {BACKLOG_YEARS}")

    # The post-adoption verifier rounds to ten decimal places. Compare its
    # UTF-8 JSON with the independently retained output fingerprint.
    post = run_output(workspace, POST_ADOPTION, "verify.py").encode("utf-8")
    if hashlib.sha256(post).hexdigest() != POST_ADOPTION_REPORT_SHA256:
        raise ValueError(f"Unexpected result at {POST_ADOPTION}")
    verify_post_adoption_controls(json.loads(post))
    verify_main_adoption_windows(workspace)

    extended = read_report(workspace, EXTENDED, "manifest.json")
    for script, expected in extended["result_sha256"].items():
        output = run_output(workspace, EXTENDED, script).encode("utf-8")
        if hashlib.sha256(output).hexdigest() != expected:
            raise ValueError(f"Unexpected result at {EXTENDED}/{script}")

    verify_record_cost_sources(workspace)
    verify_secondary_outcomes(workspace)
    verify_group_formal_shares(workspace)
    verify_quit_dynamics(workspace)

    for package, (expected, paths) in PINNED_REPORTS.items():
        output = run_output(workspace, package, "verify.py", *(workspace / path for path in paths)).encode("utf-8")
        if hashlib.sha256(output).hexdigest() != expected:
            raise ValueError(f"Unexpected result at {package}")

    for line in CORE_REPORT:
        print(line)
    print("Verified precision and multiplicity for 12 main-study comparisons.")
    print("Verified resource schedules for 5, 6, 8 and 10 hosts from 40 arms and 10 prehistories.")
    print("Verified four record-procedure contrasts across 65 paired worlds.")
    print("Verified six founding-startup size baselines and ten adjacent-size contrasts.")
    print("Verified acquisition contrasts across 120 matched worlds and 480 arm aggregates.")
    print("Verified startup regret normalization at 30 and 120 initial members.")
    print("Verified checkpoint timing and storage for four 50,000-member pilot arms.")
    print("Verified terminal stock and 12 paired estimates from 40 main-study arms.")
    print("Verified five-year post-adoption outcomes from 40 arm totals.")
    print("Verified record-day totals against the retained daily backlog history.")
    print("Verified annual stocks and the final 60 days against the daily backlog history.")
    print("Verified the extended startup, backlog and three-procedure reports.")
    print("Verified the public founding-closure cohort arithmetic.")
    for package in STANDARD_REPORTS:
        print(f"Verified {package}.")
    for package in PINNED_REPORTS:
        print(f"Verified {package}.")
    print("Verified three unadjusted secondary-outcome intervals across ten main-study worlds.")
    print("Verified absolute same-month quit-flow changes over 36 months per comparison.")
    print("Verified five-year process-control and within-witness operating and regret contrasts.")
    print("Verified operating shortfall by adoption window from 100 annual rows and 20 five-year totals.")
    print("Verified four hierarchy levels and 16 paired formal-share intervals in ten worlds.")
    print("Passed all 44 retained-result checks.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "dams-thesis-verification",
                        help="directory for the pinned input files (default: %(default)s)")
    parser.add_argument("--offline", action="store_true", help="read the cache without making HTTP requests")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("Python 3.11 or later is required")
    try:
        cache = args.cache.expanduser()
        if cache.is_symlink():
            raise ValueError(f"Cache directory must not be a symbolic link: {cache}")
        if not args.offline:
            cache.mkdir(parents=True, mode=0o700, exist_ok=True)
        opener = None if args.offline else build_opener(NoRedirect())
        with tempfile.TemporaryDirectory(prefix="dams-verification-") as directory:
            workspace = Path(directory)
            for package, commit, files in PACKAGES:
                target = workspace / package
                for name, size, digest in files:
                    output = target / name
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(obtain(cache, opener, package, commit, name, size, digest))
            verify(workspace)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
