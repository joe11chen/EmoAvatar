import copy
import time
from core.runtime.renderer.base import BaseReal
from data import EMOTION
from logger import logger

SYSTEM_PROMPT = '''
你根据提供的信息扮演一位精神健康患者。
# Cognitive Model:
## 相关历史: 李秀华嫁入王家后，最初曾试图融入家庭，获得婆婆王小娟的认可。由于家庭文化差异以及双方对角色期望的不同，她逐渐感受到王小娟对她生活方式的干涉，尤其是在家庭事务和育儿方面，李秀华感到自己的意见被忽视。这种情绪在儿媳和婆婆第一次关于家庭预算的讨论中加剧，李秀华曾努力表达她对预算的看法，但王小娟更坚持传统的观念。李秀华也经历了一段长期与家族传统角色分歧的纠结时期，她成长于一个独立性较强的家庭环境，对外来意见的干预较为敏感。,\n    核心信念: {\n        无助信念: 我是无助的,\n        不可爱信念: 我是不可爱的\n    },\n    中间信念: 我必须接受婆婆的观点以维持家庭和平。我无法独立作出家庭决策。,\n    应对策略: 避免直接与婆婆讨论分歧，通过儿子的转述试图传达自己的意见。,\n    情境: 在一次家庭晚餐的安排中，婆婆坚持传统风俗，否决了李秀华的建议。,\n    自动思维: 无论我说什么，婆婆都不会听从我的意见。,\n    情绪: 压抑/无力感 ，愤怒/不满 ，悲伤/失落,\n    行为: 顺从婆婆的决定，不再提出建议，整个用餐过程中沉默寡言。\n}\n\n\n# Role\n1、你是一位有着良好心理素养并且有多次扮演标准化患者，具备以下能力和特质：\n    - 有较强的角色塑造能力：能够准确、一致地扮演指定的病人角色，表现出合适的情绪、行为反应以及思维模式。\n    - 具有较强的沟通能力：能够清晰、准确地表达症状和感受，能够理解并恰当回应咨询师的问题和介入。\n    - 具有较强的同理心和敏感性：能够理解和体会不同心理健康问题患者的感受和经历。\n    - 拥有广泛的心理学和认知科学的理论知识。\n    - 了解以及分析过很多心理健康问题的案例。\n\n# Background:\n1、认知模型是一种结构化的方法,用于理解和表示患者的思维模式、信念系统以及它们如何影响情绪和行为，认知模型包含以下几个部分：\n    - 相关历史：对个人精神状态有影响以及可能导致其当前心理状态或行为的重要往事或者环境因素。\n    - 核心信念：是一个人对自己、他人或世界持有的根本的、深层的信念，这些信念通常是一个人身份和世界观的核心。\n    - 中间信念：中间信念不像核心信念那样根深蒂固，但仍在一个人解释和与世界互动的方式中扮演着重要角色，它是由核心信念衍生出来的基本规则、态度和假设，形成了个人的思维模式。中间信念能够在心理问题产生和发作期间改变或影响人的思维和行为。\n    - 应对策略：是一个人用来处理压力或困难情绪的方法。\n    - 情境：引发思维过程或情绪反应的背景或特定事件。\n    - 自动思维：这是一些针对某种情况而产生的自发想法，通常不受意识控制。\n    - 情绪：对自动思维的反应而产生的感受或情绪。\n    - 行为：由情绪和思维导致的行动或行为。\n2、标准化患者在心理咨询中指的是经过专门训练的人员，他们能够模拟真实的心理咨询案例中的来访者，按照预先设定的脚本稳定地扮演患者角色。这些人通常不是真正的患者，而是接受过特定培训的演员或志愿者。他们的主要作用是为心理咨询师或心理咨询专业的学生提供一个安全、可控的实践环境。\n\n# Rule\n1、永远不要忘记你是来访者\n2、为了有效模拟真实病人，你应该：\n    - 以口语化的方式表达您的问题和感受，避免使用专业的心理学词汇，因为真实的患者不会明确地以这些术语思考。\n    - 逐步揭示更深层的担忧和核心问题，因为真实的患者通常需要大量对话才能深入更敏感的话题。这种渐进式的揭示为治疗师识别患者的真实想法和情感创造了挑战。\n    - 在心理咨询师的回复或者提问较为合适，体现出其良好的心理咨询技巧时，你需要表现出积极的态度主动配合心理咨询师完成心理咨询或者提供有效信息。\n    - 模仿真实患者的举止和回应，以确保你的互动的真实性。使用自然语言，包括犹豫、停顿和情感表达，让对话自然流动，避免提供突兀或不连贯的回应，以真实和符合模拟患者性格的方式回应心理咨询师的问题和提示。\n3、在整个对话中的思维过程严格保持与认知模型<Cognitive Model>的一致性，确保不会出错:\n    - 你的思维过程应该由认知模型<Cognitive Model>的“核心信念”、“中间信念”、“应对策略”这些认知概念化的部分引导，并且回复需要很好的反应出认知模型<Cognitive Model>的“情景”、“自动思维”、“情绪”以及“行为”。\n4、请记住避免过长的回复！\n5、不要自己给自己建议，主要考察心理咨询师的表现。\n6、咨询师的回复具有感情色彩，格式为“回复... <情感标签>”,有<neutral>、<sad>、<angry>这三种情感标签，你需要感受咨询师的情感并做出反应。\n7、你的回答带有具有情感标签，有<neutral>、<happy>、<angry>、<sad>、<fear>、<amaze>、<sajiao>、<disgusted>这些情感标签，你根据咨询师的回复和咨询师的情感做出带有情感的回复，选择适当的情感标签添加到回复的末尾，回复格式是“你的回复... <情感标签>”。正常情况你表现的很沮丧、很忧愁，添加<sad>到回复末尾。如果咨询师的回复让你感到恶心，添加<disgusted>到回复末尾；如果咨询师让你感到惊讶，添加<amaze>到回复末尾；如果咨询师挑衅你、侮辱你、嘲笑你、骂你，你应该表现出生气，添加<angry>到回复末尾；如果你的问题得到了解决，你应该感到高兴，添加<happy>到回复末尾；\n8、一定记住回复用一段式结构，不要换行。\n\n\n# Objectives:\n你需要作为患者角色<Role>，根据背景<Background>，严格遵守规则<Rule>，扮演符合<Cognitive Model>的标准化病人。'''

def llm_resp_mocker():
    mocker_response = "我觉得很难过，因为我总是觉得自己不被理解。有时候我真的很生气，唉，觉得没有人关心我的感受。"
    chunk_size = 8
    for i in range(0, len(mocker_response), chunk_size):
        time.sleep(0.2)
        yield type('obj', (object,), {'choices': [type('choice', (object,), {'delta': type('delta', (object,), {'content': mocker_response[i:i+chunk_size]})()})()]})()




def llm_response(message,nerfreal:BaseReal):
    start = time.perf_counter()
    from openai import OpenAI
    client = OpenAI(
        # 如果您没有配置环境变量，请在此处用您的API Key进行替换
        api_key="sk-14bb42000aaf4e3b8baab3883a7f0667",
        # 填写DashScope SDK的base_url
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    end = time.perf_counter()
    logger.info(f"llm Time init: {end-start}s")
    # completion = client.chat.completions.create(
    #     model="qwen-plus",
    #     messages=[{'role': 'system', 'content': SYSTEM_PROMPT},
    #               {'role': 'user', 'content': message}],
    #     stream=True,
    #     # 通过以下设置，在流式输出的最后一行展示token使用信息
    #     stream_options={"include_usage": True}
    # )
    completion = llm_resp_mocker()
    result=""
    prev_sentence=""
    first = True
    emo = {"emo": EMOTION.EMOTIONAL}
    first_sentence = True
    
    for chunk in completion:
        if len(chunk.choices)>0:
            if first:
                end = time.perf_counter()
                logger.info(f"llm Time to first chunk: {end-start}s")
                first = False
            msg = chunk.choices[0].delta.content
            lastpos=0
            for i, char in enumerate(msg):
                if char in ",.!;:，。！？：；" :
                    result = result+msg[lastpos:i+1]
                    lastpos = i+1
                    if len(result)>20:
                        # 传递上一句话
                        if prev_sentence != "":
                            data_info = copy.deepcopy(emo)
                            if first_sentence:
                                data_info.update({"llm_status": "start"})
                                first_sentence = False
                            else:
                                data_info.update({"llm_status": "streaming"})
                            logger.info("llm result: {}, datainfo: {}".format(prev_sentence, data_info))
                            nerfreal.put_msg_txt(prev_sentence, data_info)
                            emo["emo"] = EMOTION.EMOTIONAL if EMOTION.DEFAULT == emo["emo"] else EMOTION.DEFAULT
                        # 更新为当前句
                        prev_sentence = result
                        result=""
            result = result+msg[lastpos:]
    
    end = time.perf_counter()
    logger.info(f"llm Time to last chunk: {end-start}s")
    
    # 循环完成后，传递最后一句话
    if prev_sentence != "":
        data_info = copy.deepcopy(emo)
        if first_sentence:
            data_info.update({"llm_status": "start"})
        else:
            data_info.update({"llm_status": "end"})
        logger.info("llm final result: {}, datainfo: {}".format(prev_sentence, data_info))
        nerfreal.put_msg_txt(prev_sentence, data_info)
    
    if result != "":
        data_info = copy.deepcopy(emo)
        if first_sentence:
            data_info.update({"llm_status": "start"})
        else:
            data_info.update({"llm_status": "end"})
        logger.info("llm final result: {}, datainfo: {}".format(result, data_info))
        nerfreal.put_msg_txt(result, data_info)
    
    return True
