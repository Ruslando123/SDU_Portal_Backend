import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    kotlin("jvm") version "2.4.21"
    kotlin("plugin.spring") version "2.4.21"
    id("org.springframework.boot") version "4.1.2"
}

group = "org.example.sdu"
version = "0.1.0-SNAPSHOT"

springBoot { mainClass = "org.example.sdu.registration.RegistrationApplicationKt" }

repositories { mavenCentral() }

kotlin {
    jvmToolchain(21)
    compilerOptions {
        jvmTarget = JvmTarget.JVM_21
        freeCompilerArgs.add("-Xjsr305=strict")
    }
}

dependencies {
    implementation(platform("org.springframework.boot:spring-boot-dependencies:4.1.2"))
    implementation(platform("org.jetbrains.kotlin:kotlin-bom:2.4.21"))
    implementation("org.springframework.boot:spring-boot-starter-webmvc")
    implementation("org.springframework.boot:spring-boot-starter-jdbc")
    implementation("org.springframework.boot:spring-boot-starter-flyway")
    implementation("org.springframework.boot:spring-boot-starter-security")
    implementation("org.springframework.boot:spring-boot-starter-session-jdbc")
    implementation("org.springframework.boot:spring-boot-starter-data-redis")
    implementation("org.springframework.session:spring-session-data-redis")
    implementation("org.springframework.boot:spring-boot-starter-actuator")
    implementation("com.rabbitmq:amqp-client")
    runtimeOnly("io.micrometer:micrometer-registry-prometheus")
    implementation("org.jetbrains.kotlin:kotlin-reflect:2.4.21")
    runtimeOnly("org.postgresql:postgresql")
    runtimeOnly("org.flywaydb:flyway-database-postgresql")

    // The requested JUnit 5 engine is independent of Spring 7's JUnit 6 extension.
    testImplementation(enforcedPlatform("org.junit:junit-bom:5.14.4"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
    testImplementation("org.testcontainers:testcontainers-postgresql:2.0.5")
    testImplementation("org.postgresql:postgresql")
}

tasks.test {
    useJUnitPlatform { excludeTags("correctness-stress") }
    maxParallelForks = 1
    inputs.file(rootDir.parentFile.resolve("docs/database.md")).withPropertyName("physicalDictionary")
    inputs.files(fileTree("src/main/kotlin") { include("**/*.kt") }).withPropertyName("moduleBoundarySources")
    inputs.file(rootDir.parentFile.resolve("infrastructure/modal/start-redis-session.sh")).withPropertyName("redisSessionAcl")
    systemProperty("project.root", rootDir.parentFile.absolutePath)
    systemProperty("junit.jupiter.execution.parallel.enabled", "false")
    testLogging { events("passed", "skipped", "failed") }
}

tasks.register<JavaExec>("migrate") {
    group = "database"
    description = "Apply and validate Flyway migrations using environment credentials."
    classpath = sourceSets.main.get().runtimeClasspath
    mainClass = "org.example.sdu.registration.bootstrap.database.MigrationCli"
}

dependencyLocking { lockAllConfigurations() }

tasks.register<JavaExec>("syntheticAccount") {
    group = "database"
    description = "Create an isolated synthetic account; read its secret from a terminal or environment."
    classpath = sourceSets.main.get().runtimeClasspath
    mainClass = "org.example.sdu.registration.authentication.internal.provisioning.SyntheticAccountCli"
    standardInput = System.`in`
}

tasks.register<Test>("correctnessStress") {
    group = "verification"
    description = "Explicit PostgreSQL correctness scenario: 2000 distinct students, 30 seats."
    testClassesDirs = sourceSets.test.get().output.classesDirs
    classpath = sourceSets.test.get().runtimeClasspath
    useJUnitPlatform { includeTags("correctness-stress") }
    systemProperty("correctness.stress.workers", providers.gradleProperty("stressWorkers").getOrElse("32"))
    maxParallelForks = 1
    systemProperty("project.root", rootDir.parentFile.absolutePath)
    testLogging { events("passed", "skipped", "failed"); showStandardStreams = true }
    outputs.upToDateWhen { false }
}
