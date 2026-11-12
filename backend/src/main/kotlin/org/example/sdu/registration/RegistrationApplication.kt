package org.example.sdu.registration

import org.springframework.boot.autoconfigure.SpringBootApplication
import org.springframework.boot.data.redis.autoconfigure.DataRedisAutoConfiguration
import org.springframework.boot.data.redis.autoconfigure.DataRedisRepositoriesAutoConfiguration
import org.springframework.boot.runApplication

@SpringBootApplication(exclude = [DataRedisAutoConfiguration::class, DataRedisRepositoriesAutoConfiguration::class])
class RegistrationApplication

fun main(args: Array<String>) {
    runApplication<RegistrationApplication>(*args)
}
